import os
import json
import uuid
import chromadb
from typing import Any, cast
import math
import sqlite3
from openai import AsyncOpenAI
from datetime import datetime

from .time_engine import TimeEngine
from .config_manager import config_manager 

class MemoryManager:
    def __init__(self, max_history: int = 10):
        self.history: list[dict[str, Any]] = []
        
        # 1. 初始化 ChromaDB (關聯拓樸層)
        db_path = os.path.join(os.path.dirname(__file__), "../chroma_db")
        self.chroma_client = chromadb.PersistentClient(path=db_path)
        self.collection = self.chroma_client.get_or_create_collection(name="murasame_memory")
        
        # 2. 初始化 SQLite (全資訊層)
        self.sqlite_path = os.path.join(os.path.dirname(__file__), "../chat_history.db")
        self.conn = sqlite3.connect(self.sqlite_path, check_same_thread=False)
        self.cursor = self.conn.cursor()
        self.cursor.execute('''CREATE TABLE IF NOT EXISTS logs
                             (id INTEGER PRIMARY KEY AUTOINCREMENT, role TEXT, content TEXT, timestamp DATETIME)''')
        self.conn.commit()

        # 3. 記憶防疲勞冷卻字典
        self.last_recalled: dict[str, datetime] = {}
        
        # 4. 自述錨點儲存路徑
        self.anchor_path = os.path.join(os.path.dirname(__file__), "../latest_anchor.txt")

    def add_message(self, role: str, content: str):
        # 對話去重防禦
        if self.history and self.history[-1].get("role") == role:
            if self.history[-1].get("parts", [""])[0] == content:
                return

        self.history.append({"role": role, "parts": [content]})
        max_turns = int(config_manager.get("max_history_turns", 10)) * 2
        if len(self.history) > max_turns:
            self.history = self.history[-max_turns:]
            
        timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        self.cursor.execute("INSERT INTO logs (role, content, timestamp) VALUES (?, ?, ?)", (role, content, timestamp))
        self.conn.commit()

    def get_messages(self) -> list[dict[str, Any]]:
        return self.history

    def clear(self) -> None:
        self.history = []

    def _get_fuzzy_time(self, past_dt: datetime) -> str:
        delta = datetime.now() - past_dt
        if delta.days == 0:
            return "不久前"
        elif delta.days < 7:
            return f"{delta.days} 天前"
        elif delta.days < 30:
            return f"{delta.days // 7} 週前"
        else:
            return f"{delta.days // 30} 個月前"

    def search_long_term_memory(self, query: str, n_results: int = 5) -> str:
        if self.collection.count() == 0:
            return ""
            
        results = self.collection.query(
            query_texts=[query],
            n_results=min(n_results, self.collection.count())
        )
        
        ids_list = results.get("ids")
        distances_list = results.get("distances")
        metadatas_list = results.get("metadatas")
        documents_list = results.get("documents")

        if not ids_list or len(ids_list[0]) == 0 or not distances_list or not metadatas_list or not documents_list:
            return ""

        best_memory_str: str = ""
        highest_w: float = 0.0
        now = datetime.now()
        
        # 🔥 新增：用來記錄最終勝出的記憶，以便後續進行鞏固更新
        best_doc_id: str | None = None
        best_meta: dict[str, Any] | None = None

        # 2. 遍歷候選記憶
        for i in range(len(ids_list[0])):
            doc_id: str = str(ids_list[0][i])
            meta_raw = metadatas_list[0][i]
            meta = cast(dict[str, Any], meta_raw) if isinstance(meta_raw, dict) else {}
            distance: float  = distances_list[0][i]
            document: str = str(documents_list[0][i])
            
            sim: float = 1.0 / (1.0 + distance)
            n_tags: int = min(int(str(meta.get("tags_count", 1))), 10)
            
            # (3) 動態時間衰減 (含永久記憶機制與喚醒加權)
            created_at_str = str(meta.get("created_at", now.strftime("%Y-%m-%d %H:%M:%S")))
            created_at = datetime.strptime(created_at_str, "%Y-%m-%d %H:%M:%S")
            delta_days: int = max(0, (now - created_at).days)
            
            s_base = int(meta.get("s_base", 50))
            recall_count = int(meta.get("recall_count", 0))
            
            # 🔥 新增：如果 S_base 達到 95 分以上，視為「永久核心記憶」，時間不會使其衰減
            if s_base >= 95:
                v_t: float = 1.0
            else:
                # 決定基礎衰減率
                base_decay = 0.005 if s_base >= 80 else 0.05
                # 每次成功喚醒，衰減率打 8 折（記憶越想越牢固，下限為 0.001）
                effective_decay = max(0.001, base_decay * (0.8 ** recall_count))
                v_t: float = math.exp(-effective_decay * delta_days)
            
            p_c: float = 1.0
            last_recalled_time = self.last_recalled.get(doc_id)
            if last_recalled_time:
                hours_since_recall: float = (now - last_recalled_time).total_seconds() / 3600.0
                if hours_since_recall < 4.0:
                    p_c = 0.1  
                elif hours_since_recall < 24.0:
                    p_c = 0.5  
            
            # 讓高相似度 (0.9) 依然很高 (0.81)，但低相似度 (0.6) 瞬間掉下去 (0.36)
            sharpened_sim = sim ** 2 
            w_m: float = (0.7 * sharpened_sim + 0.3 * (n_tags / 10.0)) * v_t * p_c
            
            print(f"🧠 [記憶評估] ID: {doc_id[-6:]} | Sim: {sim:.2f} | V_t: {v_t:.2f} (Recall: {recall_count}) | P_c: {p_c} => W = {w_m:.3f}")

            if w_m > highest_w and w_m >= 0.45:
                highest_w = w_m
                best_doc_id = doc_id  # 記錄勝出者 ID
                best_meta = meta      # 記錄勝出者 Metadata
                
                # 3. 觸發 SQLite 全資訊層切片 (動態對話時間窗)
                sql_id_raw = meta.get("sqlite_id")
                if sql_id_raw is not None:
                    sql_id: int = int(str(sql_id_raw))
                    
                    # (1) 先取得這筆核心記憶發生的精準時間
                    self.cursor.execute("SELECT timestamp FROM logs WHERE id = ?", (sql_id,))
                    target_row = self.cursor.fetchone()
                    
                    if target_row and target_row[0]:
                        target_time_str = target_row[0]
                        
                        # (2) 動態切片：抓取上下 6 句，並且「嚴格限制在前後 15 分鐘內」的關聯對話
                        # 這樣既能包覆完整的事件脈絡，又能完美避開「隔天早上」的無關對話
                        query = """
                            SELECT role, content 
                            FROM logs 
                            WHERE id >= ? AND id <= ?
                              AND timestamp >= datetime(?, '-15 minutes')
                              AND timestamp <= datetime(?, '+15 minutes')
                            ORDER BY id ASC
                        """
                        self.cursor.execute(query, (sql_id - 6, sql_id + 6, target_time_str, target_time_str))
                        slice_rows = self.cursor.fetchall()
                    else:
                        # 防呆機制：如果時間戳找不到，退回基礎的固定上下 2 句抓法
                        self.cursor.execute(
                            "SELECT role, content FROM logs WHERE id >= ? AND id <= ? ORDER BY id ASC", 
                            (sql_id - 2, sql_id + 2)
                        )
                        slice_rows = self.cursor.fetchall()
                    
                    # (3) 組合上下文
                    context_slice: list[str] = []
                    for row in slice_rows:
                        speaker = "主人" if str(row[0]) == "user" else "叢雨"
                        context_slice.append(f"{speaker}：{str(row[1])}")
                    
                    fuzzy_time = self._get_fuzzy_time(created_at)
                    best_memory_str = (
                        f"【腦海中浮現的深刻記憶片段 ({fuzzy_time})】\n"
                        f"事件標籤：{document}\n"
                        f"當時的對話上下文：\n" + "\n".join(context_slice)
                    )
                    self.last_recalled[doc_id] = now

        # 🔥 新增：當迴圈結束，若有記憶成功被喚醒，更新其 recall_count 鞏固記憶
        if best_doc_id and best_meta:
            current_recall_count = int(best_meta.get("recall_count", 0)) + 1
            updated_meta = {k: v for k, v in best_meta.items()}
            updated_meta["recall_count"] = current_recall_count
            
            try:
                self.collection.update(
                    ids=[best_doc_id],
                    metadatas=[updated_meta]
                )
                print(f"🌟 [記憶鞏固] ID: {best_doc_id[-6:]} 記憶韌性提升！目前喚醒次數: {current_recall_count}")
            except Exception as e:
                print(f"⚠️ [記憶鞏固失敗]: {e}")

        return best_memory_str

    async def extract_and_save_memory(self, user_text: str, time_engine: TimeEngine) -> None:
        raw_key = config_manager.get("openai_api_key", config_manager.get("api_key", ""))
        api_key = raw_key if raw_key else "sk-dummy-key"
        client = AsyncOpenAI(api_key=api_key, base_url=config_manager.get("base_url", None))
        model_name = str(config_manager.get("model", "gpt-4o-mini"))

        try:
            prompt = f"""
            當前系統時間：{time_engine.get_time_context()}
            請評估主人剛才說的話：「{user_text}」
            
            【S_base 記憶深度打分指南 (多維度綜合評估)】
            請綜合評估「資訊價值」、「情感強烈度」與「對未來關係的影響力」，給出 0-100 的 S_base：
            - [0-29] 毫無記憶價值：無意義的語氣詞、隨機亂碼 (例如：「嗯」、「好」)。
            - [30-49] 日常短期記憶：吃喝拉撒、例行公事、低情緒波動 (例如：「我今天吃麵」、「去開會」)。
            - [50-79] 中期重要記憶：具體事件、明確的情緒起伏 (例如：「剛修完一個超難的 Bug，快累死了」)。
            - [80-94] 長期深刻記憶：興趣喜好、強烈的情感依賴、共同經歷的重要回憶、深刻的約定 (例如：「之前我搞砸時多虧有妳陪我，週末一起去吃蛋糕吧」)。
            - [95-100] 永久核心記憶：人生重大轉折、極度強烈的情感羈絆、對系統或彼此關係的底層定義、重要的紀念日 (例如：「妳是我無可取代的依靠」、「我升職了」)。

            🔥 ⚠️ 【自述錨點 (anchor) 撰寫絕對守則】⚠️ 🔥
            當 S_base >= 80 時，你就是「叢雨本人」來寫這段內心獨白（anchor），這將成為她的靈魂記憶：
            1. 人設綁定：自稱必須是「本座」，稱呼對方為「主人」。
            2. 性格特質：稍微有點傲嬌但同時你喜歡甜食、撒嬌。表面上有時會吐槽主人，但內心深處其實非常高興、心疼且依賴主人。
            3. 絕對禁止機器感：嚴禁出現「核心模組」、「處理單元」、「數據」、「運算」、「程式」、「AI」等詞彙。本座是有靈魂的少女，不是冷冰冰的系統！
            
            
            【標籤數量限制規則 (嚴格執行)】
            - S_base < 50：最多打 2 個標籤。
            - S_base 50~79：可打 3~5 個標籤。
            - S_base >= 80：可打 6~10 個標籤。

           【三層記憶打標任務】
            請嚴格輸出 JSON 格式：
            {{
                "S_base": 0,
                "tags": ["標籤1", "標籤2"],
                "anchor": "第一人稱內心獨白(S_base>=80才填)",
                "is_unresolved": false,
                "parent_id": null,
                "has_event": false,
                "fact": "客觀事實句",
                "event_time": null, 
                "is_future_reminder": false,
                "is_yearly": false
            }}
            """
            
            response = await client.chat.completions.create(
                model=model_name,
                messages=[{"role": "system", "content": "你是一個精準的記憶評估引擎，嚴格輸出 JSON。"}, {"role": "user", "content": prompt}],
                response_format={"type": "json_object"}
            )
            
            result_text = response.choices[0].message.content or "{}"
            start_idx = result_text.find('{')
            end_idx = result_text.rfind('}')
            if start_idx != -1 and end_idx != -1:
                result_text = result_text[start_idx:end_idx+1]
                
            data = json.loads(result_text)
            
            s_base = int(data.get("S_base", 0))
            tags = data.get("tags", [])
            anchor = data.get("anchor", "")
            
            if s_base >= 30 and tags:
                self.cursor.execute("SELECT MAX(id) FROM logs")
                fetch_result = self.cursor.fetchone()
                last_sql_id: int = int(fetch_result[0]) if fetch_result and fetch_result[0] else 0
                
                doc_id = f"mem_{uuid.uuid4().hex[:8]}"
                
                metadata: dict[str, Any] = {
                    "created_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
                    "sqlite_id": last_sql_id,
                    "s_base": s_base,
                    "tags_count": len(tags),
                    "is_unresolved": bool(data.get("is_unresolved", False)),
                    "type": "fact",
                    "recall_count": 0  # 🔥 新增：初始喚醒次數為 0
                }
                tags_str = ", ".join(tags)
                self.collection.add(documents=[tags_str], metadatas=[metadata], ids=[doc_id])
                print(f"📌 [拓樸層打標] {tags_str} (S_base: {s_base})")

            # 2. 處理自述錨點 (S_base >= 80)
            if s_base >= 80 and anchor:
                # 🌟 將 "w" 改成 "a"，代表在檔案尾端附加內容
                with open(self.anchor_path, "a", encoding="utf-8") as f:
                    # 🌟 記得加上換行符號 \n，避免所有自述黏成同一行
                    f.write(anchor + "\n")
                print(f"🔥 [生成自述錨點] {anchor}")

            await time_engine.process_extracted_memory(data)

        except Exception as e:
            print(f"⚠️ [記憶萃取失敗]: {e}")