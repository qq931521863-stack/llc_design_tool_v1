"""Markdown/CSV/JSON calculation-book export."""

from __future__ import annotations

import json
import math
from dataclasses import asdict
from enum import Enum
from pathlib import Path

import pandas as pd

from ..core.config import create_project_document
from ..magnetics.core import CoreDatabase
from ..models.system import SystemAnalysis
from ..plotting.plots import create_all_plots


def _jsonable(value):
    if isinstance(value, Enum):
        return value.value
    if isinstance(value, complex):
        return {"real": value.real, "imag": value.imag}
    if isinstance(value, float) and not math.isfinite(value):
        # Keep output.json strict-JSON compatible; non-finite values encode as null.
        return None
    if isinstance(value, dict):
        return {k: _jsonable(v) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [_jsonable(v) for v in value]
    return value


def integrated_magnetic_record(spec) -> dict | None:
    """Expose exact electrical/physical identities without claiming hardware proof."""
    if not spec.integrated_magnetics:
        return None
    from ..core.integrated_magnetics import from_spec
    model = from_spec(spec)
    target = spec.integrated_target_lr_h
    return {
        "mode": spec.magnetics_mode.value,
        "source": spec.integrated_inductance_source.value,
        "characterized_core": spec.integrated_characterized_core,
        "configured_interwinding_gap_m": spec.integrated_interwinding_gap_m,
        "configured_interlayer_gap_m": spec.integrated_interlayer_gap_m,
        "minimum_insulation_m": spec.integrated_minimum_insulation_m,
        "geometry_relative_tolerance": spec.integrated_geometry_tolerance,
        "characterized_primary_turns": spec.integrated_characterized_primary_turns,
        "characterized_secondary_turns": spec.integrated_characterized_secondary_turns,
        "characterization_frequency_hz": spec.integrated_measurement_frequency_hz,
        "characterization_note": spec.integrated_measurement_note,
        "physical_matrix": asdict(model),
        "primary_short_h": model.lr_h,
        "apr_lr_h": model.lr_h, "apr_lm_h": model.lm_h,
        "apr_effective_turns_ratio": model.effective_turns_ratio,
        "physical_magnetizing_h": model.physical_magnetizing_h,
        "physical_primary_leakage_h": model.primary_leakage_h,
        "physical_secondary_leakage_h": model.secondary_leakage_h,
        "coupling": model.coupling,
        "target_lr_h": target,
        "target_relative_error": abs(model.lr_h - target) / target if target else None,
        "target_relative_tolerance": spec.integrated_target_tolerance,
        "external_resonant_inductor_present": False,
        "scope": "Linear two-winding APR equivalent; physical core/winding losses counted once. "
                 "Entered characterization and target agreement do not certify a constructed transformer, "
                 "frequency-independent permeability, nonlinear saturation or multi-winding rectification.",
    }


def operating_point_rows(analysis: SystemAnalysis) -> list[dict]:
    rows = []
    for point in analysis.operating_points:
        op = point.operating_point
        row = {
            "label": point.label,
            "magnetics_mode": analysis.spec.magnetics_mode.value,
            "physical_turns_ratio": analysis.spec.turns_ratio,
            "electrical_turns_ratio": analysis.spec.electrical_turns_ratio,
            "external_resonant_inductor_present": analysis.resonant_inductor is not None,
            "vbus_v": op.vbus_v,
            "load_pct": op.load_fraction * 100.0,
            "pout_w": op.pout_w,
            "operating_point_source": "FHA_OPERATING_POINT",
            "frequency_khz": op.switching_frequency_hz / 1e3,
            "normalized_frequency": op.normalized_frequency,
            "required_gain": op.required_gain,
            "phase_deg": op.input_phase_deg,
            "q_effective": op.q_effective,
            "resonant_current_rms_a": op.resonant_current_rms_a,
            "resonant_current_peak_a": op.resonant_current_peak_a,
            "magnetizing_current_peak_a": op.magnetizing_current_peak_a,
            "secondary_current_rms_a": op.secondary_current_rms_a,
            "commutation_current_a": op.commutation_current_a,
            "zvs_charge_margin": point.primary.zvs_charge_margin,
            "zvs_energy_margin": point.primary.zvs_energy_margin,
            "output_ripple_vpp": point.output_capacitor.total_ripple_vpp,
            "transformer_flux_dc_t": point.transformer.b_dc_t,
            "magnetic_source": point.magnetic_source,
            "capacitor_source": point.capacitor_source,
            "passive_frequency_hz": point.passive_frequency_hz or op.switching_frequency_hz,
            "commutation_source": point.commutation_source,
            "loss_waveform_sha256": point.loss_waveform_sha256,
            "semiconductor_source": point.semiconductor_source,
            "semiconductor_frequency_hz": point.semiconductor_frequency_hz or op.switching_frequency_hz,
            "primary_device_rms_current_a": point.primary.device_rms_current_a,
            "sr_device_rms_current_a": point.synchronous_rectifier.device_rms_current_a,
            "total_loss_w": point.total_loss_w,
            "efficiency_pct": point.efficiency * 100.0,
        }
        row.update({f"{component}_source": source
                    for component, source in point.component_sources().items()})
        row.update(point.breakdown())
        row.update(point.detailed_magnetics_breakdown())
        rows.append(row)
    return rows


def write_markdown_report(analysis: SystemAnalysis, path: str | Path) -> Path:
    output = Path(path)
    nominal = analysis.nominal
    spec, tank = analysis.spec, analysis.tank
    t = analysis.transformer
    ind = analysis.resonant_inductor
    bus = analysis.bus_capacitor
    integrated = integrated_magnetic_record(spec)
    if ind is None:
        if integrated is None:
            raise ValueError("Missing external resonant-inductor design in separate mode")
        inductor_lines = [
            "## 5. 集成漏感谐振：无独立 Lr 磁件",
            "",
            "- Lr 是同一变压器的 APR 等效漏感，不额外生成磁芯、绕组、气隙、BOM 或热节点。",
            "- 独立电感损耗列为 0，仅用于账务兼容；共同磁芯与两绕组损耗已计入变压器一次。",
            "- 实体主磁化电感与 APR Lm 不同；实体主磁通由两绕组电流映射求得。",
            f"- 表征来源：{spec.integrated_measurement_note}",
            f"- 表征频率：{spec.integrated_measurement_frequency_hz:g} Hz；模式：{spec.integrated_inductance_source.value}",
            "",
            "```json",
            json.dumps(integrated, ensure_ascii=False, indent=2),
            "```",
            "",
        ]
        screen = t.integrated_leakage
        if screen is not None:
            inductor_lines.extend([
                "### 绕组场能与漏感可实现性筛查", "",
                f"- 状态：{screen.status}；本检查不会把平衡场能直接当作 APR Lshort。",
                f"- 配置/表征平衡漏感：{screen.balanced_geometry_h*1e6:.6g} / {screen.characterized_balanced_h*1e6:.6g} µH",
                f"- 每个 P-S 界面配置/所需间距：{screen.configured_interwinding_gap_m*1e3:.6g} / {screen.required_interwinding_gap_m*1e3:.6g} mm",
                f"- 所需径向构建 / 可用窗口高度：{screen.required_radial_build_m*1e3:.6g} / {screen.available_window_height_m*1e3:.6g} mm",
                f"- 几何 APR Lr 界限：{screen.geometry_apr_lr_lower_h*1e6:.6g}–{screen.geometry_apr_lr_upper_h*1e6:.6g} µH；未凭空分配一次/二次漏感。",
                "```json", json.dumps(asdict(screen), ensure_ascii=False, indent=2), "```", "",
            ])
    else:
        inductor_lines = [
            "## 5. 外置谐振电感初算", "",
            f"- 磁芯：`{ind.core.part_number}`，{ind.core.family}/{ind.core.shape}，材料 {ind.core.material}",
            f"- 有效参数：Ae={ind.core.ae_mm2:.1f} mm²，Amin={ind.core.amin_mm2:.1f} mm²，Aw={ind.core.aw_mm2:.1f} mm²，Ve={ind.core.ve_mm3/1000:.1f} cm³",
            f"- 匝数：{ind.turns} T", f"- 绕线：{ind.wire.description}",
            f"- 层数：{ind.layers}（限制 ≤ {spec.resonant_inductor_max_layers}）",
            f"- 总气隙：{ind.gap_total_mm:.3f} mm",
            f"- 窗口利用率：{ind.fill_factor*100:.2f}%",
            f"- 最差 Bpk（Amin）：{ind.worst_b_peak_t:.4f} T",
            f"- 标称热点估算：{nominal.resonant_inductor.estimated_hotspot_c:.1f} °C",
            f"- 标称磁芯/铜损：{nominal.resonant_inductor.core_w:.3f} / {nominal.resonant_inductor.copper_w:.3f} W", "",
        ]
    lines = [
        f"# LLC {'半桥' if spec.primary_topology.value == 'HALF_BRIDGE' else '全桥'} + 全桥同步整流 V2 磁性损耗计算书",
        "",
        "> 版权说明：工具设计人 **杨帅** · 开关电源仿真与实用设计",
        "",
        "> **磁性模型等级：高级工程模型。** 磁芯使用温度插值 iGSE 对实际重构 B(t) 积分；Litz 铜损按逐谐波、圆股精确集肤、层间 MMF、束内换位、端接和气隙边缘场分解。磁材系数和标准磁芯几何仍需在硬件定版前用准确厂家曲线与样件校准。",
        "",
        "## 1. 设计输入",
        "",
        "| 项目 | 数值 |",
        "|---|---:|",
        f"| 母线额定/范围 | {spec.vbus_nom_v:.0f} V / {spec.vbus_hold_end_v:.0f}–{spec.vbus_max_v:.0f} V |",
        f"| 输出 | {spec.vout_v:.1f} V / {spec.pout_w/1000:.2f} kW / {spec.output_current_a:.2f} A |",
        f"| 一次侧 | {spec.primary_topology.value} |",
        f"| 二次侧 | {spec.secondary_topology.value} |",
        f"| 频率 | {spec.minimum_frequency_hz/1e3:.1f}–{spec.maximum_frequency_hz/1e3:.1f} kHz，fr={spec.resonant_frequency_hz/1e3:.1f} kHz |",
        f"| 实体匝数/匝比 | {spec.primary_turns}:{spec.secondary_turns} / {spec.turns_ratio:.4f} |",
        f"| 电气等效匝比 / 磁件模式 | {spec.electrical_turns_ratio:.6f} / {spec.magnetics_mode.value} |",
        f"| Litz 单丝裸铜直径 | {spec.litz_strand_copper_diameter_m*1e3:.3f} mm |",
        f"| 母线电容/维持时间 | {spec.bus_capacitance_f*1e6:.0f} µF / {spec.requested_hold_time_s*1e3:.1f} ms |",
        "",
        "## 2. 谐振腔结果",
        "",
        "| 参数 | 结果 |",
        "|---|---:|",
        f"| Ln = Lm/Lr | {tank.ln_ratio:.4f} |",
        f"| 满载 Qe | {tank.q_full_load:.4f} |",
        f"| Rac（额定满载、一次侧折算） | {tank.rac_nom_ohm:.3f} Ω |",
        f"| Zr | {tank.zr_ohm:.3f} Ω |",
        f"| Lr | {tank.lr_h*1e6:.3f} µH |",
        f"| Cr | {tank.cr_f*1e9:.3f} nF |",
        f"| Lm | {tank.lm_h*1e6:.3f} µH |",
        f"| fm | {tank.fm_hz/1e3:.3f} kHz |",
        "",
        "## 3. 标称点结果",
        "",
        "| 项目 | 结果 |",
        "|---|---:|",
        f"| FHA 开关频率 | {nominal.operating_point.switching_frequency_hz/1e3:.3f} kHz |",
        f"| FHA 输入阻抗相角 | {nominal.operating_point.input_phase_deg:.3f}° |",
        f"| FHA 谐振电流 RMS/峰值 | {nominal.operating_point.resonant_current_rms_a:.3f} / {nominal.operating_point.resonant_current_peak_a:.3f} A |",
        f"| FHA 二次电流 RMS/峰值 | {nominal.operating_point.secondary_current_rms_a:.3f} / {nominal.operating_point.secondary_current_peak_a:.3f} A |",
        f"| ZVS 电荷/能量裕量 | {nominal.primary.zvs_charge_margin:.2f} / {nominal.primary.zvs_energy_margin:.2f} |",
        f"| 器件电流来源 | {nominal.semiconductor_source} |",
        f"| 磁件电流/磁通来源 | {nominal.magnetic_source} |",
        f"| 电容电流/电压来源 | {nominal.capacitor_source} |",
        f"| 换流/ZVS 来源 | {nominal.commutation_source} |",
        f"| 损耗波形 SHA256 | {nominal.loss_waveform_sha256 or 'N/A'} |",
        f"| 器件损耗频率 | {(nominal.semiconductor_frequency_hz or nominal.operating_point.switching_frequency_hz)/1e3:.3f} kHz |",
        f"| 主开关/SR 单器件 RMS | {nominal.primary.device_rms_current_a:.3f} / {nominal.synchronous_rectifier.device_rms_current_a:.3f} A |",
        f"| 总损耗 | {nominal.total_loss_w:.3f} W |",
        f"| 估算效率 | {nominal.efficiency*100:.3f}% |",
        f"| 输出电容纹波电流 RMS | {nominal.output_capacitor.capacitor_current_rms_a:.3f} A |",
        f"| 输出纹波 | {nominal.output_capacitor.total_ripple_vpp:.3f} Vpp |",
        "",
        "## 4. 变压器初算",
        "",
        f"- 磁芯：`{t.core.part_number}`，{t.core.family}/{t.core.shape}，{t.core.manufacturer}，材料 {t.core.material}",
        f"- 有效参数：Ae={t.core.ae_mm2:.1f} mm²，Amin={t.core.amin_mm2:.1f} mm²，Aw={t.core.aw_mm2:.1f} mm²，Ve={t.core.ve_mm3/1000:.1f} cm³",
        f"- 绕组结构：`P{spec.primary_turns//2}-S{spec.secondary_turns}-P{spec.primary_turns-spec.primary_turns//2}`",
        f"- 一次线：{t.primary_wire.description}；每个半绕组 {t.primary_layers_per_half} 层",
        f"- 二次线：{t.secondary_wire.description}；{t.secondary_layers} 层",
        f"- 窗口利用率：{t.fill_factor*100:.2f}%",
        f"- 径向构建：{t.radial_build_mm:.2f} mm / 窗口高度 {t.core.window_height_mm:.2f} mm",
        f"- 实体主磁化电感所需总气隙：{t.gap_total_mm:.3f} mm（Lmu={spec.physical_magnetizing_inductance_h(tank.lm_h)*1e6:.3f} µH）",
        f"- 最差 Bpk（Amin）：{t.worst_b_peak_t:.4f} T",
        f"- 标称热点估算：{nominal.transformer.estimated_hotspot_c:.1f} °C",
        f"- 标称磁芯/一次铜/二次铜损：{nominal.transformer.core_w:.3f} / {nominal.transformer.primary_copper_w:.3f} / {nominal.transformer.secondary_copper_w:.3f} W",
        "",
        *inductor_lines,
        "### 自动磁芯选型前列候选",
        "",
        "| 部件 | 排名 | 磁芯 | 家族 | 匝数 | 标称损耗 | 热点 | Bpk | 气隙 |",
        "|---|---:|---|---|---:|---:|---:|---:|---:|",
        *[f"| 变压器 | {x.rank} | {x.part_number} | {x.family} | {spec.primary_turns}:{spec.secondary_turns} | {x.nominal_total_loss_w:.3f} W | {x.nominal_hotspot_c:.1f} °C | {x.worst_b_peak_t:.3f} T | {x.gap_total_mm:.2f} mm |" for x in t.alternatives[:5]],
        *[f"| 谐振电感 | {x.rank} | {x.part_number} | {x.family} | {x.turns} | {x.nominal_total_loss_w:.3f} W | {x.nominal_hotspot_c:.1f} °C | {x.worst_b_peak_t:.3f} T | {x.gap_total_mm:.2f} mm |" for x in (ind.alternatives[:5] if ind is not None else ())],
        "",
        "## 6. 标称磁性损耗深度分解",
        "",
        "| 损耗机制 | W |",
        "|---|---:|",
        *[f"| {key} | {value:.5f} |" for key, value in nominal.detailed_magnetics_breakdown().items()],
        "",
        "## 7. 母线维持时间",
        "",
        f"- 安装容量：{bus.installed_capacitance_f*1e6:.1f} µF",
        f"- 400 V 放电到 {spec.vbus_hold_end_v:.0f} V 的估算时间：{bus.hold_time_to_limit_s*1e3:.3f} ms",
        f"- 指定 {spec.requested_hold_time_s*1e3:.1f} ms 所需容量：{bus.required_capacitance_f*1e6:.1f} µF",
        f"- 指定时间结束时母线电压：{bus.predicted_end_voltage_v:.2f} V",
        "",
        "## 8. 工作点表",
        "",
        "| 母线 | 负载 | fs | 增益 | 相角 | Ir RMS | Is RMS | 损耗 | 效率 |",
        "|---:|---:|---:|---:|---:|---:|---:|---:|---:|",
    ]
    for point in analysis.operating_points:
        op = point.operating_point
        lines.append(
            f"| {op.vbus_v:.0f} V | {op.load_fraction*100:.0f}% | "
            f"{op.switching_frequency_hz/1e3:.2f} kHz | {op.required_gain:.3f} | "
            f"{op.input_phase_deg:.2f}° | {op.resonant_current_rms_a:.2f} A | "
            f"{op.secondary_current_rms_a:.2f} A | {point.total_loss_w:.2f} W | "
            f"{point.efficiency*100:.2f}% |")
    lines.extend(["", "## 9. 可行性", "",
                  f"**结论：{'通过 V2 磁性初筛' if analysis.feasible else '未通过 V2 磁性初筛'}**", ""])
    if analysis.feasibility_reasons:
        lines.extend(f"- 失败：{reason}" for reason in analysis.feasibility_reasons)
    else:
        lines.append("- 当前硬约束未发现失败项。")
    lines.extend(["", "## 10. 模型警告", ""])
    lines.extend(f"- {warning}" for warning in analysis.warnings)
    lines.extend(["", "## 11. 图表", "",
                  "![增益曲线](gain_curves.png)", "",
                  "![损耗分解](loss_breakdown.png)", "",
                  "![磁性损耗深度分解](magnetics_loss_breakdown.png)", "",
                  "![效率工作点](efficiency_workpoints.png)", ""])
    output.write_text("\n".join(lines), encoding="utf-8")
    return output


def export_calculation_book(analysis: SystemAnalysis,
                            output_directory: str | Path) -> dict[str, Path]:
    out = Path(output_directory)
    out.mkdir(parents=True, exist_ok=True)
    paths: dict[str, Path] = {}
    paths["report"] = write_markdown_report(analysis, out / "LLC_calculation_book.md")
    rows = operating_point_rows(analysis)
    paths["operating_points"] = out / "operating_points.csv"
    pd.DataFrame(rows).to_csv(paths["operating_points"], index=False)
    paths["loss_breakdown_csv"] = out / "nominal_loss_breakdown.csv"
    pd.DataFrame([{"loss_item": key, "loss_w": value}
                  for key, value in analysis.nominal.breakdown().items()]).to_csv(
                      paths["loss_breakdown_csv"], index=False)
    paths["magnetics_breakdown_csv"] = out / "nominal_magnetics_breakdown.csv"
    pd.DataFrame([{"loss_item": key, "loss_w": value}
                  for key, value in analysis.nominal.detailed_magnetics_breakdown().items()]).to_csv(
                      paths["magnetics_breakdown_csv"], index=False)
    paths["core_library_csv"] = out / "magnetic_core_library.csv"
    core_rows = []
    for core in CoreDatabase().cores:
        core_rows.append({
            "part_number": core.part_number, "family": core.family,
            "shape": core.shape, "manufacturer": core.manufacturer,
            "material": core.material, "purposes": ",".join(core.purposes),
            "ae_mm2": core.ae_mm2, "amin_mm2": core.amin_mm2,
            "aw_mm2": core.aw_mm2, "ve_mm3": core.ve_mm3,
            "le_mm": core.le_mm, "window_width_mm": core.window_width_mm,
            "window_height_mm": core.window_height_mm,
            "mlt_primary_mm": core.mlt_primary_mm,
            "thermal_resistance_k_per_w": core.thermal_resistance_k_per_w,
            "core_mass_g": core.core_mass_g, "cost_usd": core.cost_usd,
        })
    pd.DataFrame(core_rows).to_csv(paths["core_library_csv"], index=False)
    paths["transformer_candidates_csv"] = out / "transformer_core_candidates.csv"
    pd.DataFrame([asdict(x) for x in analysis.transformer.alternatives]).to_csv(
        paths["transformer_candidates_csv"], index=False)
    if analysis.resonant_inductor is not None:
        paths["inductor_candidates_csv"] = out / "resonant_inductor_core_candidates.csv"
        pd.DataFrame([asdict(x) for x in analysis.resonant_inductor.alternatives]).to_csv(
            paths["inductor_candidates_csv"], index=False)
    else:
        # Reusing an output folder must not leave an older external-Lr candidate
        # file masquerading as part of this integrated design.
        stale = out / "resonant_inductor_core_candidates.csv"
        if stale.exists():
            stale.unlink()
    paths["result_json"] = out / "analysis.json"
    payload = create_project_document(analysis.spec, analysis)
    payload.update({
        "tank": _jsonable(asdict(analysis.tank)),
        "transformer": _jsonable(asdict(analysis.transformer)),
        "resonant_inductor": (_jsonable(asdict(analysis.resonant_inductor))
                              if analysis.resonant_inductor is not None else None),
        "integrated_magnetics": integrated_magnetic_record(analysis.spec),
        "bus_capacitor": _jsonable(asdict(analysis.bus_capacitor)),
        "feasible": analysis.feasible,
        "feasibility_reasons": list(analysis.feasibility_reasons),
        "warnings": list(analysis.warnings),
        "operating_points": rows,
    })
    paths["result_json"].write_text(json.dumps(payload, ensure_ascii=False, indent=2),
                                    encoding="utf-8")
    for plot_path in create_all_plots(analysis, out):
        paths[f"plot_{plot_path.stem}"] = plot_path
    return paths
