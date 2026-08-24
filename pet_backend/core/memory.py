import os
import json
import uuid
import chromadb
from typing import Any
from openai import AsyncOpenAI

# 引入 TimeEngine 型別供 Pylance 檢查使用
from .time_engine import TimeEngine

config_path = os.path.join(os.path.dirname(__file__), "../config.json")
with open(config_path, "r", encoding="utf-8") as f:
    config = json.load(f)
    # 將預設的 sub_model 替換為 OpenAI 的模型名稱
    SUB_MODEL_NAME = str(config.get("sub_model", "gpt-4o-mini"))

# 初始化 OpenAI 客戶端
api_key = config.get("openai_api_key", config.get("api_key", ""))
base_url = config.get("base_url", None)

client = AsyncOpenAI(
    api_key=api_key,
    base_url=base_url
)

class MemoryManager:
    def __init__(self, max_history: int = 10):
        self.history: list[dict[str, Any]] = []
        self.max_history = max_history

        db_path = os.path.join(os.path.dirname(__file__), "../chroma_db")
        self.chroma_client = chromadb.PersistentClient(path=db_path)
        self.collection = self.chroma_client.get_or_create_collection(name="murasame_memory")

    def add_message(self, role: str, content: str):
        # 這裡維持原本的 parts 格式不變，因為 brain.py 中的轉換邏輯已兼容此寫法
        self.history.append({"role": role, "parts": [content]})
        if len(self.history) > self.max_history:
            self.history.pop(0)

    def get_messages(self) -> list[dict[str, Any]]:
        return self.history

    def clear(self) -> None:
        self.history = []

    def add_long_term_memory(self, memory_text: str) -> None:
        doc_id = str(uuid.uuid4())
        self.collection.add(
            documents=[memory_text],
            ids=[doc_id]
        )

    def search_long_term_memory(self, query: str, n_results: int = 2) -> str:
        if self.collection.count() == 0:
            return ""
            
        results = self.collection.query(
            query_texts=[query],
            n_results=min(n_results, self.collection.count())
        )
        
        documents = results.get("documents")
        if not documents or len(documents) == 0 or not documents[0]:
            return ""
            
        memory_list: list[str] = [str(m) for m in documents[0]]
        return "【腦海中浮現的相關過去記憶】：\n" + "\n".join(memory_list)

    async def extract_and_save_memory(self, user_text: str, time_engine: TimeEngine, model_name: str = SUB_MODEL_NAME) -> None:
        result_text = ""
        try:
            prompt = f"""
            當前系統時間：{time_engine.get_time_context()}
            請分析以下這句使用者說的話：「{user_text}」

            【嚴格記憶篩選標準】
            請判斷這句話是否包含「有長期記憶價值」的個人資訊。
            ✅ 必須記錄（has_event: true）：
            1. 明確的個人偏好、習慣、身份背景（例：「我不吃香菜」、「我習慣用Mac」）。
            2. 明確的承諾、未來計畫、或要求系統定時提醒的指令（例：「明天下午三點提醒我開會」、「下週二我要去台北」）。
            
            ❌ 絕對不可記錄（has_event: false）：
            1. 提問、徵詢意見（例：「晚餐吃什麼？」、「你覺得哪個好？」）。
            2. 當下情緒、閒聊、打招呼（例：「今天好累」、「早安」、「哈哈」）。
            3. 當下操作指令（例：「幫我寫程式」、「講個笑話」、「查詢天氣」）。

            請嚴格只回傳 JSON 格式字串，格式如下：
            {{
              "has_event": true 或 false,
              "fact": "若是 true，請濃縮成一句客觀、精煉的事實句（如：主人不吃香菜）；若是 false，請填 null",
              "event_time": "YYYY-MM-DD HH:MM:SS" (若是全天事件如生日、紀念日，請預設填寫該日的 06:00:00。若是未來具體時間，請精準換算。若無明確時間或為常態習慣則填 null),
              "is_future_reminder": true 或 false (只有在需要未來特定時間點主動提醒時，才設為 true),
              "is_yearly": true 或 false (如果這是每年固定發生的日子，例如生日、紀念日、節日，請設為 true)
            }}
            """
            
            # 使用 OpenAI API 進行記憶萃取，並強制 JSON 輸出
            response = await client.chat.completions.create(
                model=model_name,
                messages=[
                    {"role": "system", "content": "你是一個精準的記憶萃取系統，請嚴格按照指示輸出 JSON 格式。"},
                    {"role": "user", "content": prompt}
                ],
                response_format={"type": "json_object"}
            )
            
            result_text = response.choices[0].message.content
            if not result_text:
                raise ValueError("回傳內容為空")

            result_text = result_text.strip()
            
            # 兼容處理，以防模型仍包裝了 Markdown
            if result_text.startswith("```json"):
                result_text = result_text[7:]
            if result_text.startswith("```"):
                result_text = result_text[3:]
            if result_text.endswith("```"):
                result_text = result_text[:-3]
            result_text = result_text.strip()

            event_data = json.loads(result_text)
            
            await time_engine.process_extracted_memory(event_data)
            
        except json.JSONDecodeError:
            print(f"[記憶萃取失敗]: LLM 沒有回傳有效的 JSON。原始回覆：{result_text}")
        except Exception as e:
            print(f"[記憶萃取失敗]: {e}")