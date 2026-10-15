"""Shell translations for the DAB and two-level dq workspaces."""

_TEXT = {
    "DAB TPS 与三相 dq 模型": ("DAB TPS and three-phase dq models", "DAB TPS と三相 dq モデル", "DAB TPS 및 3상 dq 모델"),
    "DAB 工程": ("DAB project", "DAB プロジェクト", "DAB 프로젝트"),
    "DAB 设计参数": ("DAB design parameters", "DAB 設計パラメータ", "DAB 설계 파라미터"),
    "PLL 锁相": ("PLL synchronization", "PLL 同期", "PLL 동기화"),
    "SVPWM 六路": ("Six-gate SVPWM", "6 ゲート SVPWM", "6 게이트 SVPWM"),
    "TPS / 功率": ("TPS / Power", "TPS / 電力", "TPS / 전력"),
    "dq 电流环": ("dq current loops", "dq 電流ループ", "dq 전류 루프"),
    "三相电网 / 电流": ("Three-phase grid / Current", "三相電源 / 電流", "3상 계통 / 전류"),
    "保存与导出": ("Save and export", "保存とエクスポート", "저장 및 내보내기"),
    "保存参数 JSON": ("Save parameters JSON", "パラメータ JSON を保存", "파라미터 JSON 저장"),
    "保存工程": ("Save project", "プロジェクトを保存", "프로젝트 저장"),
    "加载参数 JSON": ("Load parameters JSON", "パラメータ JSON を読み込み", "파라미터 JSON 불러오기"),
    "加载工程": ("Load project", "プロジェクトを読み込み", "프로젝트 불러오기"),
    "参数与相位约定": ("Parameter and phase conventions", "パラメータと位相の規約", "파라미터 및 위상 규약"),
    "导出波形 CSV + 指标 JSON": ("Export waveform CSV + metrics JSON", "波形 CSV + 指標 JSON を出力", "파형 CSV + 지표 JSON 내보내기"),
    "导出结果 CSV": ("Export results CSV", "結果 CSV を出力", "결과 CSV 내보내기"),
    "总览 / 工况表": ("Overview / Operating points", "概要 / 動作点", "개요 / 동작점"),
    "总览 / 模型边界": ("Overview / Model limits", "概要 / モデルの範囲", "개요 / 모델 한계"),
    "换流 / PWM": ("Commutation / PWM", "転流 / PWM", "전류 전환 / PWM"),
    "模型边界": ("Model limits", "モデルの範囲", "모델 한계"),
    "母线 / 能量": ("DC bus / Energy", "DC バス / エネルギー", "DC 버스 / 에너지"),
    "电容应力": ("Capacitor stress", "コンデンサストレス", "커패시터 스트레스"),
    "电气波形": ("Electrical waveforms", "電気波形", "전기 파형"),
    "电流 / 回流": ("Current / Backflow", "電流 / 逆流電力", "전류 / 역류 전력"),
    "运行 TPS 扫描": ("Run TPS sweep", "TPS スイープを実行", "TPS 스윕 실행"),
    "运行 dq / PLL 仿真": ("Run dq / PLL simulation", "dq / PLL シミュレーションを実行", "dq / PLL 시뮬레이션 실행"),
    "电源设计工具箱 — DAB TPS": ("Power Design Toolkit — DAB TPS", "電源設計ツールキット — DAB TPS", "전원 설계 도구 — DAB TPS"),
    "电源设计工具箱 — PFC Engineering: TTPL / Vienna / Two-Level dq": ("Power Design Toolkit — PFC: TTPL / Vienna / Two-Level dq", "電源設計ツールキット — PFC: TTPL / Vienna / 2 レベル dq", "전원 설계 도구 — PFC: TTPL / Vienna / 2레벨 dq"),
    "五个工作区": ("Five workspaces", "5 つのワークスペース", "다섯 작업 공간"),
    "三相两电平 dq / PLL": ("Three-phase two-level dq / PLL", "三相 2 レベル dq / PLL", "3상 2레벨 dq / PLL"),
}

CATALOGUES = {code: {key: values[i] for key, values in _TEXT.items()}
              for i, code in enumerate(("en", "ja", "ko"))}
