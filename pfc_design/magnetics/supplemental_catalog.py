"""Independent companion documents: no merging of vendors or model identities."""
import json
import math
from pathlib import Path

PFC_CASE_ENGINEERING_REVIEW = (
    "软件独立审查：案例 ΔI=(Vo−Vinpeak)²/(L·fs·Vo)，与标准 Boost 伏秒式 "
    "Vinpeak·(Vo−Vinpeak)/(L·fs·Vo) 不同。这里忠实复现案例算术，不能用此值作为真实 PFC 求解器的正确性基准。"
)

PATH = Path(__file__).resolve().parents[2] / "engineering_data" / "magnetic_supplemental_records.json"


def load_supplemental_catalog():
    data = json.loads(PATH.read_text(encoding="utf-8"))
    documents = {d["file_name"]: d for d in data["documents"]}
    def check(value):
        if isinstance(value, dict):
            if "file_name" in value and "physical_page" in value:
                document = documents.get(value["file_name"])
                page = value["physical_page"]
                if document is None or type(page) is not int or not 1 <= page <= document["page_count"]:
                    raise ValueError("Invalid supplemental source/page reference")
            for child in value.values(): check(child)
        elif isinstance(value, list):
            for child in value: check(child)
    check(data)
    return data


def replay_pfc_document_example(data=None):
    """Reproduce APP-023 arithmetic only; this is NOT a selectable core preset.

    Constants and equations follow the five-page case, including its original
    mixed-vendor and out-of-chart assumptions. No fit is used in CSC records.
    """
    data = data or load_supplemental_catalog()
    example = next(x for x in data["application_cases"] if x["id"] == "APP-023")
    models = {x["id"]: x for x in data["model_records"]}
    bias = models[example["model_ids"]["dc_bias_60mu"]]
    loss = models[example["model_ids"]["core_loss_40_60_75_90mu"]]
    if bias["formula_as_printed"] != "%mu = a / (1 + (H/b)^c) + d" or loss["formula_as_printed"] != "Pcv = 4.4332 * Bm^2.1896 * f + 0.0198 * (Bm*f)^2":
        raise ValueError("Unsupported source-example expression revision")
    # Native calculation constants from source pp.1–5, not app defaults.
    vo = 264 * math.sqrt(2) / .97 + 25 / 2
    power = vo * 2.5
    current = math.sqrt(2) * power / 175
    turns, al_uh, le_cm, ve_cm3, fs_hz = 54, .081, 9.84, 10.549, 120000
    h_oe = .4 * math.pi * turns * current / le_cm
    c = bias["coefficients"]
    fraction = .01 * (c["a"] / (1 + (h_oe / c["b"]) ** c["c"]) + c["d"])
    l_min_uh = fraction * turns**2 * al_uh
    ripple = (vo - math.sqrt(2) * 175)**2 / (l_min_uh * 1e-6 * fs_hz * vo)
    bac_t = 60 * (4e-7 * math.pi) * turns * ripple / (le_cm * .01) / 2
    c = loss["coefficients"]
    pv = c["a"] * (10 * bac_t)**c["b"] * (fs_hz / 1000) + c["c"] * (10 * bac_t * fs_hz / 1000)**2
    core_w = pv * ve_cm3 / 1000
    wire_length = turns * .0543
    wire_area = math.pi * (.0012 / 2)**2
    wire_r = 1.1 * 1.68e-8 * wire_length / wire_area
    wire_w = current**2 / 2 * wire_r
    return {"source_case": "APP-023", "scope": "原文算术复算；混合厂家资料，非 CSC 实测、非器件等效性/适用域认证",
            "Lpfc_min_uH": l_min_uh, "Bac_T": bac_t, "Pcv_mW_per_cm3": pv,
            "Pcore_W": core_w, "Pwire_W": wire_w, "PL_W": core_w + wire_w,
            "L_shortfall_vs_200uH_percent": 100 * (200 - l_min_uh) / 200,
            "engineering_cautions": [*example["engineering_cautions"], PFC_CASE_ENGINEERING_REVIEW], "sources": example["sources"]}
