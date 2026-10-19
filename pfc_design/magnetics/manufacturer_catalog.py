"""Source-specific magnetic catalog records; never inherit legacy material defaults.

A printed part, a geometry, and a material/AL availability row are different
identities. PDF coordinates are 1-based physical pages, not printed page labels.
Formula arithmetic is retained separately from a qualified application model.
"""
from __future__ import annotations

from copy import deepcopy
import hashlib
import json
import math
from pathlib import Path

CATALOG_PATH = Path(__file__).resolve().parents[2] / "engineering_data" / "csc_magnetic_catalog.json"
SCHEMA = "power-design-toolkit-source-magnetic-selection-v1"


def _positive(value, name):
    if type(value) not in (int, float) or not math.isfinite(value) or value <= 0:
        raise ValueError(f"{name}: a finite positive value is required")
    return float(value)


def _integer(value, name):
    if type(value) is not int or value < 1:
        raise ValueError(f"{name}: a positive integer is required")
    return value


def _identity(data):
    return hashlib.sha256(json.dumps(data, ensure_ascii=False, sort_keys=True,
                                     allow_nan=False, separators=(",", ":")).encode()).hexdigest()


def source_text(source: dict) -> str:
    text = f"{source.get('document', '?')} · PDF p.{source.get('pdf_page', '?')}"
    if source.get("printed_page"):
        text += f" · printed {source['printed_page']}"
    if source.get("section"):
        text += f" · {source['section']}"
    return text


def _sources(value):
    if isinstance(value, dict):
        if "document" in value and "pdf_page" in value:
            yield value
        for child in value.values():
            yield from _sources(child)
    elif isinstance(value, list):
        for child in value:
            yield from _sources(child)


def evaluate_printed_formula(model: dict, **native_inputs: float) -> float:
    """Evaluate the exact printed expression in its native units.

    This is formula arithmetic, NOT validation of a waveform, temperature,
    extrapolation, or B convention. Callers must label it accordingly.
    """
    if model.get("conflict_ids") or model.get("conflicts") or model.get("issues"):
        raise ValueError("Printed formula has unresolved source conflicts")
    coefficients = model.get("coefficients") or {}
    if model.get("model_type") == "dc_bias":
        if model.get("equation") != "percent_initial_permeability = 1 / (a + b * H_Oe ** c)":
            raise ValueError("Unsupported DC-bias expression")
        if model.get("input_units") != {"H": "Oe"}:
            raise ValueError("Unsupported DC-bias input units")
        h = native_inputs.get("H_Oe")
        if type(h) not in (int, float) or not math.isfinite(h) or h < 0:
            raise ValueError("H_Oe must be finite and nonnegative")
        a, b, c = (_positive(coefficients.get(k), k) for k in ("a", "b", "c"))
        return 1.0 / (a + b * h ** c)
    if model.get("model_type") == "core_loss":
        if model.get("equation") != "Pv_mW_cm3 = B_kG ** beta * (a * f_kHz + b * f_kHz ** gamma)":
            raise ValueError("Unsupported loss expression")
        if model.get("input_units") != {"B": "kG", "f": "kHz"} or model.get("output_unit") != "mW/cm3":
            raise ValueError("Unsupported loss formula units")
        b_kg = _positive(native_inputs.get("B_kG"), "B_kG")
        f_khz = _positive(native_inputs.get("f_kHz"), "f_kHz")
        beta, a, b, gamma = (_positive(coefficients.get(k), k) for k in ("beta", "a", "b", "gamma"))
        return b_kg ** beta * (a * f_khz + b * f_khz ** gamma)
    raise ValueError(f"Unsupported printed formula: {model.get('model_type')}")


class ManufacturerCoreCatalog:
    """Read-only source records. No implicit part-number or model synthesis."""

    def __init__(self, path: str | Path | None = None, *, data: dict | None = None):
        self.data = deepcopy(data) if data is not None else json.loads((Path(path) if path else CATALOG_PATH).read_text(encoding="utf-8"))
        self.documents = {d["filename"]: d for d in self.data["source_documents"]}
        self.geometries = self._index("geometries")
        self.models = self._index("material_models")
        self.records = {}
        self.record_types = {}
        for table, kind in (("part_records", "explicit_part"), ("availability_rows", "availability"), ("geometries", "geometry")):
            for row in self.data.get(table, []):
                key = row["record_id"]
                if key in self.records:
                    raise ValueError(f"Duplicate catalog identity: {key}")
                self.records[key] = row
                self.record_types[key] = kind
                if row.get("geometry_id") and row["geometry_id"] not in self.geometries:
                    raise ValueError(f"Unresolved geometry_id: {key}")
                for field in ("dc_bias_model_id", "loss_model_id"):
                    if row.get(field) and row[field] not in self.models:
                        raise ValueError(f"Unresolved {field}: {key}")
        for source in _sources(self.data):
            document = self.documents.get(source["document"])
            if document is None:
                raise ValueError(f"Unknown source document: {source['document']}")
            page = source["pdf_page"]
            if type(page) is not int or not 1 <= page <= document["pdf_page_count"]:
                raise ValueError(f"Invalid physical PDF page: {source}")
        for document in self.documents.values():
            digest = document["sha256"]
            if len(digest) != 64 or any(c not in "0123456789abcdef" for c in digest):
                raise ValueError("Invalid source SHA256")

    def _index(self, table):
        rows = self.data.get(table, [])
        indexed = {r["record_id"]: r for r in rows}
        if len(indexed) != len(rows):
            raise ValueError(f"Duplicate source identities in {table}")
        return indexed

    def query(self, text="", *, kind=None):
        words = text.casefold().split()
        return [self.select(key) for key, record in self.records.items()
                if (kind is None or self.record_types[key] == kind)
                and all(word in json.dumps(record, ensure_ascii=False).casefold() for word in words)]

    def select(self, record_id):
        record = self.records[record_id]
        geometry = record if self.record_types[record_id] == "geometry" else self.geometries.get(record.get("geometry_id"))
        bias = self.models.get(record.get("dc_bias_model_id"))
        loss = self.models.get(record.get("loss_model_id"))
        linked_ids = {record_id, record.get("geometry_id"), record.get("dc_bias_model_id"), record.get("loss_model_id")}
        conflict_ids = set(record.get("conflict_ids", []))
        for item in (geometry, bias, loss):
            if item:
                conflict_ids.update(item.get("conflict_ids", []))
        conflicts = [c for c in self.data.get("conflicts", []) if c.get("conflict_id") in conflict_ids
                     or linked_ids.intersection(c.get("affected_record_ids", []))]
        payload = {"schema": SCHEMA, "record_type": self.record_types[record_id],
                   "record": deepcopy(record), "geometry": deepcopy(geometry),
                   "dc_bias_model": deepcopy(bias), "loss_model": deepcopy(loss),
                   "source_documents": deepcopy(list(self.documents.values())),
                   "catalog_conflicts": deepcopy(conflicts)}
        return SourceCoreSelection(payload)


class SourceCoreSelection:
    def __init__(self, snapshot):
        self.snapshot = deepcopy(snapshot)
        if self.snapshot.get("schema") != SCHEMA:
            raise ValueError("Unsupported source-core snapshot schema")
        self.record = self.snapshot["record"]
        self.geometry = self.snapshot.get("geometry") or {}
        self.record_id = self.record["record_id"]
        self.record_type = self.snapshot["record_type"]
        if self.record_type not in {"geometry", "availability", "explicit_part"}:
            raise ValueError("Unsupported catalog record type")
        # Validate embedded source/model linkage before a saved snapshot can run.
        models = [self.snapshot[k] for k in ("dc_bias_model", "loss_model") if self.snapshot.get(k)]
        data = {"source_documents": self.snapshot["source_documents"], "material_models": models,
                "geometries": [self.geometry] if self.geometry else [],
                "part_records": [self.record] if self.record_type == "explicit_part" else [],
                "availability_rows": [self.record] if self.record_type == "availability" else []}
        # Do not recursively construct selections during validation.
        ManufacturerCoreCatalog(data=data)
        self.identity = _identity(self.snapshot)

    @property
    def label(self):
        if self.record_type == "explicit_part":
            return self.record["part_number"]
        prefix = "Geometry" if self.record_type == "geometry" else "Availability (no SKU)"
        code = self.record.get("geometry_code") or self.geometry.get("geometry_code") or self.record_id
        return f"{prefix}: {code} / {self.record.get('material', '?')} / μ={self.record.get('ui', '?')}"

    @property
    def provenance(self):
        sources = {source_text(s) for s in _sources(self.snapshot) if s.get("pdf_page")}
        return tuple(sorted(sources))

    def full_pfc_blockers(self):
        """Missing prerequisites, not a claim that plotted limits are qualified."""
        missing = []
        for name, unit in (("al_nH_per_t2", "nH/T²"), ("ui", "1"), ("bs_T", "T")):
            try:
                _positive(self.record.get(name), name)
            except ValueError:
                missing.append(f"{name} [{unit}] 未知")
        for name, unit in (("ae_cm2", "cm²"), ("le_cm", "cm"), ("ve_cm3", "cm³")):
            try:
                _positive(self.geometry.get(name), name)
            except ValueError:
                missing.append(f"{name} [{unit}] 未知")
        if not self.geometry.get("nominal_dimensions_mm"):
            missing.append("标称 OD/ID/HT 未知；涂层前后最大/最小尺寸不是标称值")
        for field, label, domains in (("dc_bias_model", "DC bias", ("H_Oe", "temperature_C")),
                                     ("loss_model", "core loss", ("B_T", "frequency_Hz", "temperature_C"))):
            model = self.snapshot.get(field)
            if not model:
                missing.append(f"{label}：没有明确关联的原厂模型")
                continue
            validity = model.get("manufacturer_stated_validity") or {}
            for domain in domains:
                if validity.get(domain) is None:
                    missing.append(f"{label}：厂家适用域 {domain} 未声明（图轴范围不作保证）")
            if model.get("conflicts") or model.get("issues") or model.get("conflict_ids"):
                missing.append(f"{label}：存在未解决的源数据冲突")
        loss = self.snapshot.get("loss_model") or {}
        if loss.get("B_convention") not in {"peak_ac", "peak_ac_T"}:
            missing.append("core loss：B 的峰值/峰峰值约定未确认")
        if loss.get("waveform") not in {"sinusoidal", "sine"}:
            missing.append("core loss：损耗拟合激励波形未确认")
        if self.record.get("conflicts") or self.record.get("issues") or self.record.get("conflict_ids"):
            missing.append("磁芯记录存在未解决的源数据冲突")
        return tuple(missing)

    def calculate_zero_bias(self, turns: int, n_cores: int = 1):
        """Only L0=N²·AL for identical stacked cores, without DC/AC claims."""
        conflicts = [c for c in self.snapshot.get("catalog_conflicts", [])
                     if c.get("field") in {"al_nH_per_t2", "part_number", "ui", "material"}]
        if conflicts:
            raise ValueError("零偏磁电感依赖的 AL/材料身份存在未解决冲突：" + ", ".join(c["conflict_id"] for c in conflicts))
        _integer(turns, "turns")
        _integer(n_cores, "n_cores")
        al = _positive(self.record.get("al_nH_per_t2"), "al_nH_per_t2 [nH/T²]")
        inductance = al * turns * turns * n_cores / 1000.0
        tolerance = self.record.get("al_tolerance_percent")
        limits = None
        if tolerance is not None:
            tolerance = _positive(tolerance, "al_tolerance_percent")
            if tolerance >= 100:
                raise ValueError("AL tolerance must be below 100%")
            limits = [inductance * (1 - tolerance / 100), inductance * (1 + tolerance / 100)]
        return {"record_id": self.record_id, "selection_identity": self.identity,
                "label": self.label, "turns": turns, "n_cores": n_cores,
                "al_nH_per_t2": al, "l_zero_bias_uh": inductance,
                "l_tolerance_range_uh": limits,
                "scope": "零偏磁名义电感 L0=N²·AL；不包含 DC 偏置、交流损耗、温升或满载验证",
                "al_measurement_conditions": deepcopy(self.record.get("al_measurement_conditions")),
                "source_conflict_ids": [c["conflict_id"] for c in self.snapshot.get("catalog_conflicts", [])],
                "full_pfc_blockers": list(self.full_pfc_blockers())}


class CatalogDomainError(ValueError):
    """An evaluation leaves the explicitly selected model coverage."""


def _interval(value, name):
    if not isinstance(value, (list, tuple)) or len(value) != 2:
        raise ValueError(f"{name}: [minimum, maximum] required")
    low, high = value
    floor = -273.15 if name == "temperature_C" else 0
    if any(type(v) not in (int, float) or not math.isfinite(v) for v in value) or low < floor or high <= low:
        raise ValueError(f"{name}: finite ordered bounds above {floor} required")
    return low, high


def validate_model_declaration(declaration):
    if not isinstance(declaration, dict):
        raise ValueError("模型声明尚未提供")
    if declaration.get("B_convention") != "peak_ac":
        raise ValueError("需要明确声明出版损耗公式中的 B 为 AC 峰值（非峰峰值）")
    if declaration.get("waveform") != "sinusoidal_reference":
        raise ValueError("需要明确声明正弦参考损耗公式用于 PFC 的工程近似；不是波形修正模型")
    if declaration.get("dimension_basis") != "after_coating_limits":
        raise ValueError("需要声明采用涂层后 OD/HT 最大值和 ID 最小值进行几何估算")
    if not isinstance(declaration.get("source"), str) or not declaration["source"].strip():
        raise ValueError("用户声明需要依据/来源，不能冒充厂家说明")
    for key in ("H_Oe", "B_T", "frequency_Hz", "temperature_C"):
        _interval(declaration.get(key), key)
    temp = declaration.get("core_temperature_C")
    if type(temp) not in (int, float) or not math.isfinite(temp):
        raise ValueError("core_temperature_C required")
    lo, hi = declaration["temperature_C"]
    if not lo <= temp <= hi:
        raise ValueError("磁芯温度超出用户声明范围")
    return deepcopy(declaration)


class CatalogPowderMaterial:
    """Exact CSC expressions with an explicit user application declaration."""
    def __init__(self, selection: SourceCoreSelection, declaration):
        self.selection = selection
        self.declaration = validate_model_declaration(declaration)
        self.permeability = _positive(selection.record.get("ui"), "ui")
        self.al_nh_per_t2 = _positive(selection.record.get("al_nH_per_t2"), "AL")
        self.bias = selection.snapshot.get("dc_bias_model")
        self.loss = selection.snapshot.get("loss_model")
        if not self.bias or not self.loss:
            raise ValueError("DC bias / loss model missing; user declaration cannot replace coefficients")
        if selection.snapshot.get("catalog_conflicts"):
            raise ValueError("所选记录有源数据冲突，用户模型声明不能覆盖冲突")
        for model in (self.bias, self.loss):
            if model.get("material") != selection.record.get("material"):
                raise ValueError("Source model material identity mismatch")
            grades = model.get("ui_values", [model.get("ui")])
            if selection.record.get("ui") not in grades:
                raise ValueError("Source model permeability identity mismatch")
            if model.get("conflicts") or model.get("issues") or model.get("conflict_ids"):
                raise ValueError("Resolve source model identity conflicts before calculation")
        # Prove supported equation/unit identity before a run.
        evaluate_printed_formula(self.bias, H_Oe=0)
        evaluate_printed_formula(self.loss, B_kG=1, f_kHz=1)

    def _check(self, value, key):
        lo, hi = self.declaration[key]
        if not math.isfinite(value) or not lo <= value <= hi:
            raise CatalogDomainError(f"{key}={value:g} outside user-declared [{lo:g}, {hi:g}]")

    def permeability_percent(self, h_oe):
        self._check(h_oe, "H_Oe")
        return evaluate_printed_formula(self.bias, H_Oe=h_oe)

    def core_loss_density_mw_cm3(self, b_pk_t, frequency_hz):
        self._check(frequency_hz, "frequency_Hz")
        if b_pk_t == 0:
            # Exact expression is zero at zero swing; no loss fit extrapolation.
            return 0.0
        self._check(b_pk_t, "B_T")
        return evaluate_printed_formula(self.loss, B_kG=10 * float(b_pk_t), f_kHz=float(frequency_hz) / 1000)


def source_pfc_components(selection: SourceCoreSelection, declaration):
    from .high_flux import HighFluxCoreGeometry
    material = CatalogPowderMaterial(selection, declaration)
    geometry = selection.geometry
    dims = geometry.get("dimensions_after_coating_mm") or {}
    if geometry.get("shape") != "toroid":
        raise ValueError("Current PFC winding model requires an explicitly identified toroid")
    od, inner, ht = (_positive(dims.get(k), k) for k in ("od_max", "id_min", "ht_max"))
    if inner >= od:
        raise ValueError("Core ID must be smaller than OD")
    core = HighFluxCoreGeometry(
        core_data=f"{selection.record.get('manufacturer', '')} {selection.label} [{selection.record_id}]",
        le_mm=_positive(geometry.get("le_cm"), "Le") * 10,
        ae_mm2=_positive(geometry.get("ae_cm2"), "Ae") * 100,
        ve_mm3=_positive(geometry.get("ve_cm3"), "Ve") * 1000,
        od_mm=od, id_mm=inner, ht_mm=ht,
        bs_t=_positive(selection.record.get("bs_T"), "Bs"),
    )
    return core, material


def reference_evaluation(selection: SourceCoreSelection, *, turns=20, n_cores=1,
                         h_oe=None, b_kg=None, f_khz=None):
    result = {"record_id": selection.record_id, "selection_identity": selection.identity,
              "label": selection.label, "full_pfc_blockers": list(selection.full_pfc_blockers()),
              "scope": "出版公式参考算术，不代表 PFC 满载、波形或温度资格验证"}
    if selection.record.get("al_nH_per_t2") is not None:
        result.update(selection.calculate_zero_bias(turns, n_cores))
    if h_oe is not None:
        model = selection.snapshot.get("dc_bias_model")
        if not model:
            raise ValueError("No explicitly linked DC-bias formula")
        bounds = (model.get("plotted_domain") or {}).get("H_Oe")
        if bounds is None or not bounds[0] <= h_oe <= bounds[1]:
            raise CatalogDomainError(f"H_Oe outside published chart coverage: {bounds}")
        result["h_oe"] = h_oe
        result["reference_permeability_percent"] = evaluate_printed_formula(model, H_Oe=h_oe)
        if "l_zero_bias_uh" in result:
            result["reference_l_at_h_uh"] = result["l_zero_bias_uh"] * result["reference_permeability_percent"] / 100
    if b_kg is not None or f_khz is not None:
        model = selection.snapshot.get("loss_model")
        if not model:
            raise ValueError("No explicitly linked loss formula")
        domain = model.get("plotted_domain") or {}
        b_bounds = domain.get("B_G")
        frequencies = domain.get("f_kHz_curves")
        if b_kg is None or f_khz is None or not b_bounds or not frequencies:
            raise ValueError("Both native B[kG]/f[kHz] and published chart coverage required")
        if not b_bounds[0] <= b_kg * 1000 <= b_bounds[1] or not min(frequencies) <= f_khz <= max(frequencies):
            raise CatalogDomainError("B/f outside published chart coverage")
        result.update({"B_kG_native": b_kg, "f_kHz": f_khz,
                       "reference_pv_mW_cm3": evaluate_printed_formula(model, B_kG=b_kg, f_kHz=f_khz),
                       "loss_convention": model.get("B_convention", "UNKNOWN"),
                       "loss_scope": "Native published B variable; no inferred AC peak/peak-to-peak conversion. Chart coverage is not a validity guarantee."})
    return result
