"""软件运行时的数据状态。"""
from dataclasses import dataclass, field
from datetime import datetime


@dataclass
class State:
    # 第 1 章
    raw: object = None
    raw_path: object = None
    raw_orig: object = None
    ica: object = None
    raw_for_ica: object = None
    epochs: object = None
    power: object = None
    itc: object = None
    tfr_condition: str = ""
    history: list = field(default_factory=list)
    # 第 3 章
    group: object = None
    group_result: dict = field(default_factory=dict)
    # 第 4 章
    dec: object = None
    dec_result: dict = field(default_factory=dict)

    def record(self, text):
        self.history.append(f"[{datetime.now():%H:%M:%S}] {text}")

    def reset_after_raw(self):
        self.ica = None
        self.raw_for_ica = None
        self.epochs = None
        self.power = None
        self.itc = None

    def reset_after_epochs(self):
        self.power = None
        self.itc = None
