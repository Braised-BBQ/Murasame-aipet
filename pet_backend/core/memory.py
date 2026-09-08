import os
import json
import uuid
import chromadb
from typing import Any
from openai import AsyncOpenAI
from datetime import datetime


from .time_engine import TimeEngine
# 1. 引入 ConfigManager
from .config_manager import config_manager 

class MemoryManager:
    def __init__(self, max_history: int = 10):
        self.history: list[dict[str, Any]] = []
        db_path = os.path.join(os.path.dirname(__file__), "../chroma_db")
        self.chroma_client = chromadb.PersistentClient(path=db_path)
        self.collection = self.chroma_client.get_or_create_collection(name="murasame_memory")

    # ... (add_message, get_messages, clear, add_long_term_memory, search_long_term_memory 維持原樣) ...
    def add_message(self, role: str, content: str):
        # 1. 存入對話
        self.history.append({"role": role, "parts": [content]})
        
        # 2. 動態讀取最新的對話上限 (預設 10 輪)
        # 乘以 2 是因為一問一答算 2 筆紀錄
        max_turns = int(config_manager.get("max_history_turns", 10)) * 2
        
        # 3. 使用切片精準保留最後 max_turns 筆紀錄，完美支援熱修改縮小上限
        if len(self.history) > max_turns:
            self.history = self.history[-max_turns:]

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

    # 2. 修改 extract_and_save_memory 參數與內部實作
    # 不再使用全域預設的 SUB_MODEL_NAME，改在呼叫時動態獲取
    async def extract_and_save_memory(self, user_text: str, time_engine: TimeEngine) -> None:
        result_text = ""
        
        # 3. 動態獲取 API Key, Base URL 與模型名稱
        raw_key = config_manager.get("openai_api_key", config_manager.get("api_key", ""))
        api_key = raw_key if raw_key else "sk-dummy-key"
        base_url = config_manager.get("base_url", None)
        model_name = str(config_manager.get("sub_model", "gpt-4o-mini"))
        
        # 4. 每次記憶萃取時動態建立 AsyncOpenAI 實例
        client = AsyncOpenAI(
            api_key=api_key,
            base_url=base_url
        )

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
            
                        請嚴格只回傳 JSON 格式字串（不要有 Markdown 標記，如 ```json），格式如下：
                        {{
                        "has_event": true 或 false,
                        "fact": "若是 true，請濃縮成一句客觀、精煉的事實句（如：主人不吃香菜）；若是 false，請填 null",
                        
                        "event_time": "YYYY-MM-DD HH:MM:SS" (⚠️ 極度重要：這是『系統要觸發提醒的鬧鐘時間』，而非事件開始時間！若使用者說「中午提醒我下午3點開會」，這裡必須精算並填寫 12:00:00。若是全天事件如生日，請預設填寫該日的 06:00:00。若是未來具體時間，請精準換算。若無明確時間或為常態習慣則填 null),
                        
                        "is_future_reminder": true 或 false(只有在需要未來特定時間點主動提醒時，才設為 true),
                        "is_yearly": true 或 false(如果這是每年固定發生的日子，例如生日、紀念日、節日，請設為 true)
                        }}
                        """
            
            response = await client.chat.completions.create(
                model=model_name,
                messages=[
                    {"role": "system", "content": "你是一個精準的記憶萃取系統，請嚴格按照指示輸出 JSON 格式。"},
                    {"role": "user", "content": prompt}
                ],
                response_format={"type": "json_object"}
            )
            
            result_text = response.choices[0].message.content
            # ... (後續 JSON 處理邏輯維持原樣) ...
            if not result_text:
                raise ValueError("回傳內容為空")

            result_text = result_text.strip()
            
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

    async def consolidate_memories(self) -> None:
        """系統啟動時背景執行：掃描並濃縮重疊或衝突的長期記憶"""
        try:
            # 🌟 動態獲取 API Key 與模型名稱 (讓它能獨立運作)
            raw_key = config_manager.get("openai_api_key", config_manager.get("api_key", ""))
            api_key = raw_key if raw_key else "sk-dummy-key"
            base_url = config_manager.get("base_url", None)
            model_name = str(config_manager.get("sub_model", "gpt-4o-mini"))
            
            client = AsyncOpenAI(api_key=api_key, base_url=base_url)

            # 抓取所有一般事實記憶
            results = self.collection.get(where={"type": "fact"})
            
            # 🌟 預先把資料抽出來並加上 fallback 空陣列 (or [])，徹底消滅 None 的可能性
            ids_list: list[str] = results.get("ids") or []
            docs_list: list[Any] = results.get("documents") or []
            metas_list: list[Any] = results.get("metadatas") or []

            if len(ids_list) < 2:
                print("🔍 [記憶整併] 記憶數量不足，無需濃縮。")
                return

            memory_list: list[str] = []
            for i in range(len(ids_list)):
                doc_id = str(ids_list[i])
                
                # 🌟 安全讀取，保證陣列長度足夠且不為空
                doc = str(docs_list[i]) if i < len(docs_list) and docs_list[i] is not None else ""
                meta: dict[str, Any] = metas_list[i] if i < len(metas_list) and metas_list[i] is not None else {}
                
                created_at = str(meta.get("created_at", "未知時間"))
                memory_list.append(f"ID: {doc_id} | 時間: {created_at} | 內容: {doc}")

            memory_text = "\n".join(memory_list)

            # 準備提示詞
            prompt = f"""
            你是一位專業的記憶整理員。請分析以下這批主人的長期記憶，找出「主題高度重疊、互相矛盾、或隨時間改變」的記憶進行濃縮。
            如果某些記憶完全獨立且無衝突，請不要將它們列入濃縮清單。

            【記憶清單】：
            {memory_text}

            【濃縮原則】：
            1. 依據時間戳記判斷因果。
            2. 嚴格輸出純 JSON 格式，必須包含 `consolidated` 陣列。
            {{
                "consolidated": [
                    {{
                        "new_fact": "濃縮演進後的新事實",
                        "obsolete_ids": ["要被替換掉的舊記憶 ID 1"]
                    }}
                ]
            }}
            """

            response = await client.chat.completions.create(
                model=model_name,
                messages=[
                    {"role": "system", "content": "你是一個精準的系統，請嚴格輸出 JSON 格式。"},
                    {"role": "user", "content": prompt}
                ],
                response_format={"type": "json_object"}
            )

            result_text = response.choices[0].message.content
            if not result_text:
                return

            data = json.loads(result_text)
            consolidated_items = data.get("consolidated", [])

            for item in consolidated_items:
                new_fact = str(item.get("new_fact", ""))
                obsolete_ids = list(item.get("obsolete_ids", []))

                if new_fact and obsolete_ids:
                    self.collection.delete(ids=obsolete_ids)
                    
                    new_id = f"mem_con_{uuid.uuid4().hex[:8]}"
                    
                    # 🌟 明確標註 new_meta 為字典，安撫 Line 210
                    new_meta: dict[str, Any] = {
                        "created_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
                        "event_time_str": "none",
                        "type": "fact",
                        "is_yearly": False
                    }
                    self.collection.add(documents=[new_fact], metadatas=[new_meta], ids=[new_id])
                    print(f"🔄 [記憶整併完成] 已合併 {len(obsolete_ids)} 筆舊記憶 -> 新記憶：{new_fact}")

        except Exception as e:
            print(f"⚠️ [記憶整併失敗]: {e}")