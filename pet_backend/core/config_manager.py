import json
import os
from typing import Any

class ConfigManager:
    def __init__(self) -> None:
        self.config_path: str = os.path.join(os.path.dirname(__file__), "../config.json")
        # 明確標註 settings 是一個 key 為字串，value 為任意型別的字典
        self.settings: dict[str, Any] = {} 
        self.load()

    def load(self) -> None:
        """從硬碟讀取最新設定"""
        try:
            with open(self.config_path, "r", encoding="utf-8") as f:
                self.settings = json.load(f)
        except Exception as e:
            print(f"[Config Error] 讀取設定檔失敗: {e}")

    # 明確標註 key 為字串 (str)，default 與回傳值為任意型別 (Any)
    def get(self, key: str, default: Any = None) -> Any:
        return self.settings.get(key, default)

# 建立全域單一實例
config_manager = ConfigManager()