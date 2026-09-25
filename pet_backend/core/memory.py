import os
import json
import uuid
import chromadb
from typing import Any, cast
import math
import sqlite3
from openai import AsyncOpenAI
from datetime import datetime, timedelta


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
        
        # 4. 雙軌自述錨點儲存路徑
        self.recent_anchor_path = os.path.join(os.path.dirname(__file__), "../recent_anchor.txt")
        self.core_anchor_path = os.path.join(os.path.dirname(__file__), "../core_anchor.txt")
        
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
    def boost_memory(self, doc_id: str) -> None:
        """
        當大腦實際採用了某筆記憶後，手動呼叫此函數提升回憶次數並追溯因果鏈。
        """
        try:
            result = self.collection.get(ids=[doc_id])
            # 🔥 修正 3：加入更嚴謹的 None 檢查，解決 Pylance "List[Metadata] | None" 無法使用 len 的問題
            if not result or result.get("metadatas") is None:
                return
            
            metadatas_list = cast(list[Any], result.get("metadatas"))
            if len(metadatas_list) == 0:
                return
                
            meta_raw = metadatas_list[0]
            meta = cast(dict[str, Any], meta_raw) if isinstance(meta_raw, dict) else {}
            
            current_recall_count = int(meta.get("recall_count", 0)) + 1
            updated_meta = {k: v for k, v in meta.items()}
            updated_meta["recall_count"] = current_recall_count
            
            self.collection.update(ids=[doc_id], metadatas=[updated_meta])
            print(f"🌟 [記憶鞏固] ID: {doc_id[-6:]} 記憶韌性提升！目前喚醒次數: {current_recall_count}")
            
            current_parent_id = meta.get("parent_id")
            visited_ids = {doc_id}
            chain_depth = 0
            max_depth = 3
            
            while current_parent_id and str(current_parent_id) not in ("none", "null") and chain_depth < max_depth:
                parent_id_str = str(current_parent_id)
                if parent_id_str in visited_ids:
                    print(f"⚠️ [因果鏈防護] 偵測到邏輯死循環，已強制切斷連結：{parent_id_str}")
                    break
                    
                visited_ids.add(parent_id_str)
                
                try:
                    parent_result = self.collection.get(ids=[parent_id_str])
                    
                    # 🔥 同樣補上對 None 的嚴格檢查
                    if not parent_result or parent_result.get("metadatas") is None:
                        break
                        
                    p_metas = cast(list[Any], parent_result.get("metadatas"))
                    if len(p_metas) > 0:
                        p_meta_raw = p_metas[0]
                        parent_meta = cast(dict[str, Any], p_meta_raw) if isinstance(p_meta_raw, dict) else {}
                        
                        parent_recall = int(parent_meta.get("recall_count", 0)) + 1
                        updated_parent_meta = {k: v for k, v in parent_meta.items()}
                        updated_parent_meta["recall_count"] = parent_recall
                        self.collection.update(ids=[parent_id_str], metadatas=[updated_parent_meta])
                        
                        print(f"🔗 [因果鏈連動] 成功鞏固關聯記憶: {parent_id_str[-6:]}")
                        
                        current_parent_id = parent_meta.get("parent_id")
                        chain_depth += 1
                    else:
                        break
                except Exception as e:
                    print(f"⚠️ [因果鏈溯源失敗]: {e}")
                    break
                    
        except Exception as e:
            print(f"⚠️ [Boost Memory Error]: {e}")
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
        
    def metabolize_dead_memories(self) -> int:
        """
        清理向量庫中 V_t 已經衰減殆盡的無用日常記憶，釋放候選名額。
        """
        now = datetime.now()
        all_data = self.collection.get()
        ids_list = all_data.get("ids", [])
        metas_list = all_data.get("metadatas", [])
        
        if not ids_list or not metas_list:
            return 0
            
        # 🌟 解決 append 報錯：明確宣告這是一個裝字串的陣列
        dead_ids: list[str] = []
        
        for doc_id, meta_raw in zip(ids_list, metas_list):
            # 🌟 解決 Pylance 報錯：強制宣告 meta 是一個字典
            meta = cast(dict[str, Any], meta_raw) if isinstance(meta_raw, dict) else {}
            
            # 以下維持原本的邏輯，Pylance 就不會再對 get() 畫紅線了
            s_base = int(str(meta.get("s_base", 50)))
            recall_count = int(str(meta.get("recall_count", 0)))
            
            if s_base >= 80 or recall_count >= 3:
                continue
                
            created_at_str = str(meta.get("created_at", now.strftime("%Y-%m-%d %H:%M:%S")))
            created_at = datetime.strptime(created_at_str, "%Y-%m-%d %H:%M:%S")
            delta_days = max(0, (now - created_at).days)
            
            base_decay = 0.05
            effective_decay = max(0.001, base_decay * (0.8 ** recall_count))
            v_t = math.exp(-effective_decay * delta_days)
            
            if v_t < 0.01:
                dead_ids.append(str(doc_id))
                
        if dead_ids:
            self.collection.delete(ids=dead_ids)
            print(f"🧹 [記憶代謝] 已成功清理 {len(dead_ids)} 筆 V_t (< 0.01) 的瑣碎記憶！")
            
        return len(dead_ids)
    
# 🌟 參數改為接收 LLM 計算好的 target_days_ago 和 time_window
    def search_long_term_memory(self, query: str, n_results: int = 5, target_days_ago: int | None = None, time_window: int = 0) -> str:
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

        now = datetime.now()
        target_date = None
        window_days = time_window
        
        if target_days_ago is not None:
            target_date = now - timedelta(days=int(target_days_ago))
            print(f"⏱️ [LLM 時間對齊] 換算目標日: {target_date.strftime('%Y-%m-%d')} | 模糊半徑: ±{window_days}天")
        
        # 🔥 修正 1：明確宣告陣列的型別為 list[dict[str, Any]]，解決 Pylance 所有未知型別報錯
        candidates: list[dict[str, Any]] = []

        # 2. 遍歷候選記憶
        for i in range(len(ids_list[0])):
            doc_id: str = str(ids_list[0][i])
            meta_raw = metadatas_list[0][i]
            meta = cast(dict[str, Any], meta_raw) if isinstance(meta_raw, dict) else {}
            distance: float = float(str(distances_list[0][i]))
            document: str = str(documents_list[0][i])
            
            sim: float = 1.0 / (1.0 + distance)
            n_tags: int = min(int(str(meta.get("tags_count", 1))), 10)
            
            created_at_str = str(meta.get("created_at", now.strftime("%Y-%m-%d %H:%M:%S")))
            created_at = datetime.strptime(created_at_str, "%Y-%m-%d %H:%M:%S")
            delta_days: int = max(0, (now - created_at).days)
            
            s_base = int(meta.get("s_base", 50))
            recall_count = int(meta.get("recall_count", 0))
            
            if s_base >= 95:
                v_t: float = 1.0
            else:
                base_decay = 0.005 if s_base >= 80 else 0.05
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
            
            t_bonus = 0.0
            if target_date and window_days > 0:
                diff_days = abs((created_at - target_date).days)
                t_bonus = 0.3 * math.exp(- (diff_days ** 2) / (2 * (window_days ** 2)))

            sharpened_sim = sim ** 2 
            w_m: float = ((0.7 * sharpened_sim + 0.3 * (n_tags / 10.0)) * v_t * p_c) + t_bonus
            print(f"🧠 [評估] {doc_id[-6:]} | Sim: {sim:.2f} | V_t: {v_t:.2f} | T_bonus: {t_bonus:.3f} => W: {w_m:.3f}")

            if w_m >= 0.45:
                candidates.append({
                    "w_m": w_m,
                    "doc_id": doc_id,
                    "meta": meta,
                    "document": document,
                    "created_at": created_at
                })

        candidates.sort(key=lambda x: float(x["w_m"]), reverse=True)
        
        # 🔥 修正 2：在這裡更改喚醒數量，改成抓取前 3 名
        top_candidates = candidates[:3]
        
        if not top_candidates:
            return ""

        best_memory_str = ""
        for cand in top_candidates:
            # 加入型別轉換確保 Pylance 認得
            c_doc_id: str = str(cand["doc_id"])
            c_meta: dict[str, Any] = cast(dict[str, Any], cand["meta"])
            c_created_at: datetime = cand["created_at"]
            c_document: str = str(cand["document"])
            
            sql_id_raw = c_meta.get("sqlite_id")
            context_slice: list[str] = []
            
            if sql_id_raw is not None:
                sql_id: int = int(str(sql_id_raw))
                self.cursor.execute("SELECT timestamp FROM logs WHERE id = ?", (sql_id,))
                target_row = self.cursor.fetchone()
                
                if target_row and target_row[0]:
                    target_time_str = target_row[0]
                    query = """
                        SELECT role, content FROM logs 
                        WHERE id >= ? AND id <= ?
                          AND timestamp >= datetime(?, '-15 minutes')
                          AND timestamp <= datetime(?, '+15 minutes')
                        ORDER BY id ASC
                    """
                    # 因應 3 條記憶，這裡微調為上下 4 句以節省 Token
                    self.cursor.execute(query, (sql_id - 4, sql_id + 4, target_time_str, target_time_str))
                    slice_rows = self.cursor.fetchall()
                else:
                    self.cursor.execute(
                        "SELECT role, content FROM logs WHERE id >= ? AND id <= ? ORDER BY id ASC", 
                        (sql_id - 2, sql_id + 2)
                    )
                    slice_rows = self.cursor.fetchall()
                
                for row in slice_rows:
                    speaker = "主人" if str(row[0]) == "user" else "叢雨"
                    context_slice.append(f"{speaker}：{str(row[1])}")
                
            fuzzy_time = self._get_fuzzy_time(c_created_at)
            
            best_memory_str += (
                f"【記憶片段 ID: {c_doc_id}】 ({fuzzy_time})\n"
                f"事件標籤：{c_document}\n"
                f"當時的對話上下文：\n" + "\n".join(context_slice) + "\n\n"
            )
            self.last_recalled[c_doc_id] = now

        return best_memory_str.strip()

    async def extract_and_save_memory(self, user_text: str, model_reply: str, time_engine: TimeEngine) -> None:
        raw_key = config_manager.get("openai_api_key", config_manager.get("api_key", ""))
        api_key = raw_key if raw_key else "sk-dummy-key"
        client = AsyncOpenAI(api_key=api_key, base_url=config_manager.get("base_url", None))
        model_name = str(config_manager.get("sub_model", "gpt-4o-mini"))
         # 🔥 1. 前置聯想掃描：尋找可能的因果記憶 (Parent Memory)
        potential_parent_str = "無"
        potential_parent_id = None
        if self.collection.count() > 0:
            try:
                parent_query = self.collection.query(query_texts=[user_text], n_results=1)
                
                # 🌟 明確抽出變數，Chroma query 回傳的是雙層陣列 List[List[...]]
                q_ids = parent_query.get("ids")
                q_distances = parent_query.get("distances")
                q_docs = parent_query.get("documents")
                
                # 🌟 拔除 is not None，直接檢查列表是否為空
                if (q_ids and len(q_ids) > 0 and len(q_ids[0]) > 0 and
                    q_distances and len(q_distances) > 0 and len(q_distances[0]) > 0 and
                    q_docs and len(q_docs) > 0 and len(q_docs[0]) > 0):
                    
                    # 🌟 強制轉型為 float 和 str
                    distance = float(str(q_distances[0][0]))
                    
                    if distance < 1.2: 
                        potential_parent_id = str(q_ids[0][0])
                        parent_fact = str(q_docs[0][0])
                        potential_parent_str = f"【關聯ID: {potential_parent_id}】 內容：「{parent_fact}」"
            except Exception:
                pass

        try:
            prompt = f"""
            當前系統時間：{time_engine.get_time_context()}
            請評估主人與叢雨剛才發生的這段完整對話：
            
            主人：「{user_text}」
            叢雨：「{model_reply}」

            【對話脈絡對齊規則 (極度重要)】
            你必須根據「叢雨的實際回答」來決定這筆記憶的最終解讀。
            如果主人說話曖昧不清，但叢雨將其解讀為特定事物（例如把老朋友解讀為月亮），你的記憶標籤與事實萃取「必須完全跟隨叢雨的認知」，絕對不可自行腦補其他毫不相干的舊記憶！
                
            【S_base 記憶深度打分指南 (多維度綜合評估)】
            請綜合評估「資訊價值」、「情感強烈度」與「對未來關係的影響力」，給出 0-100 的 S_base：
            - [0-29] 毫無記憶價值：無意義的語氣詞、隨機亂碼 (例如：「嗯」、「好」)。
            - [30-49] 日常短期記憶：吃喝拉撒、例行公事、低情緒波動、日常互動、指令 (例如：「摸頭」、「我今天吃麵」、「去開會」)。
            - [50-79] 中期重要記憶：具體事件、明確的情緒起伏 (例如：「剛修完一個超難的 Bug，快累死了」)。
            - [80-94] 長期深刻記憶：強烈的情感依賴、興趣喜好、共同經歷的重要回憶、深刻的約定 、長期的習慣(例如：「之前我搞砸時多虧有妳陪我，週末一起去吃蛋糕吧」)。
            - [95-100] 永久核心記憶：人生重大轉折、極度強烈的情感羈絆、對系統或彼此關係的底層定義、重要的紀念日。

            🔥 ⚠️ 【自述錨點 (anchor) 撰寫絕對守則】⚠️ 🔥
            當 S_base >= 80 時，你就是「叢雨本人」來寫這段內心獨白（anchor），這將成為她的靈魂記憶：
            1. 人設綁定：自稱必須是「本座」，稱呼對方為「主人」。
            2. 性格特質：稍微有點傲嬌但同時你喜歡甜食、撒嬌。表面上有時會吐槽主人，但內心深處其實非常高興、心疼且依賴主人。
            3. 絕對禁止機器感：嚴禁出現「核心模組」、「處理單元」、「數據」、「程式」等詞彙。本座是有靈魂的少女！

            【標籤提取規則 (極簡維度化打標)】
            請務必從以下維度提取標籤，確保記憶具備立體的聯想力，但必須「極度俐落」：
            1. 實體(Entity)：具體人、事、物、地點 (如：鍵盤、期中考、麥當勞)。
            2. 動作(Action)：動詞或事件本質 (如：購買、搞砸、約定)。
            3. 情感(Emotion)：主人的情緒狀態 (如：焦慮、開心、疲倦)。
            4. 事實收束(Fact)：若涉及主人的「個人基本資料」或「長期絕對偏好」，強制加入 `[主人情報]` 標籤。
            5. 高分金標 (Golden Tags)：
               若 S_base >= 80，請強制從以下特殊標籤中挑選 1~2 個加入，用來收束高價值記憶：
               `[核心約定]` (未來的承諾或計畫)、`[情感羈絆]` (互相依賴、安慰的時刻)、`[重大人生]` (升學、搬家、職涯)、`[專屬秘密]` (只有你們知道的事)。
            
            6. 時間錨點 (Time Anchor)：
               若對話中明確提及「未來計畫」或「過去特定時期」，必須將時間轉化為獨立標籤！
               - ❌ 錯誤："明年二月去日本" -> 標籤只打 ["日本", "旅遊"] (未來檢索會找不到！)
               - ✅ 正確：提取出 ["日本", "旅遊", "明年", "二月", "[核心約定]"]
            【絕對排版限制】
            - 每個標籤長度嚴格限制在 2~5 個字以內。
            - 絕對禁止保留口語字綴 (如：的、了、啊) 或完整短句。
            - ❌ 壞標籤：["今天寫程式", "超多Bug", "修不好很煩"]
            - ✅ 好標籤：["程式", "Bug", "修復失敗", "煩躁"]
            
            【標籤數量限制規則 (嚴格執行)】
            - S_base 在同區間內分數越高，盡可能打越多標籤。
            - S_base < 50：最多打 2 個標籤(挑重點)。
            - S_base 50~79：可打 3~5 個標籤(盡量涵蓋三個維度)。
            - S_base 80~94：可打 6~10 個標籤(豐富細節，務必包含高分金標)。
            - S_base >= 95：可打 最多 15 個標籤(豐富細節，務必包含高分金標，可加入叢雨自己的感受標籤)。

            【🔗 因果鏈綁定 (parent_id)】
            系統剛才在腦海中閃過了這段舊記憶：{potential_parent_str}
            如果主人現在的話，明顯是這段舊記憶的「後續結果」、「起因」或「強烈關聯」，請在 JSON 的 parent_id 欄位填入該 ID。
            如果無太大關聯，請務必填入 null。

            🌟 【時間引擎與事實萃取標準 (針對 has_event)】
            請判斷這句話是否包含「有長期記憶價值」或「需要排程提醒」的資訊。
            ✅ 必須記錄（has_event: true）：
            1. 明確的個人偏好、習慣、身份背景（例：「我不吃香菜」）。
            2. 承諾、未來計畫、或要求系統定時提醒的指令（例：「明天下午三點提醒我」）。
            ❌ 絕對不可記錄（has_event: false）：
            提問、徵詢意見、當下情緒、閒聊、打招呼、當下的操作指令（如查詢天氣）。

            【三層記憶打標任務】
            請嚴格輸出 JSON 格式：
            {{
                "S_base": 0,
                "tags": ["標籤1", "標籤2"],
                "anchor": "第一人稱內心獨白(S_base>=80才填，否則填空字串)",
                "is_unresolved": false,
                "parent_id": null,
                "has_event": true 或 false,
                "fact": "若是 true，請濃縮成一句客觀、精煉的事實句；若是 false，請填 null",
                "event_time": "YYYY-MM-DD HH:MM:SS" (⚠️ 極度重要：這是『系統要觸發提醒的鬧鐘時間』，而非事件開始時間！若說「中午提醒我下午3點開會」，須精算填 12:00:00。若是全天事件如生日、節日，請預設填寫該日的 06:00:00。若無具體提醒時間請嚴格填 null),
                "is_future_reminder": true 或 false (只有在需要未來特定時間點主動提醒時，且只有在「第一次建立」這個計畫，或是「修改」舊時間時才設為 true。如果主人只是在「回顧、討論或確認」已經約好的舊計畫，必須強制設為 false),
                "is_yearly": true 或 false (如果是每年固定發生的日子，例如生日、紀念日，請設為 true)
            }}
            """
            
            response = await client.chat.completions.create(
                model=model_name,
                messages=[{"role": "system", "content": "你是一個精準的記憶評估引擎，嚴格輸出 JSON。"}, {"role": "user", "content": prompt}],
                response_format={"type": "json_object"},
                reasoning_effort="low"  # 👈 僅允許模型進行極簡的推導，限制思考長度
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
            parent_id = data.get("parent_id") # 讀取 LLM 判斷的因果鏈
            
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
                    "recall_count": 0,
                    "parent_id": str(parent_id) if parent_id else "none" # 🔥 存入資料庫
                }
                tags_str = ", ".join(tags)
                self.collection.add(documents=[tags_str], metadatas=[metadata], ids=[doc_id])
                print(f"📌 [拓樸層打標] {tags_str} (S_base: {s_base}) | 🔗 因果綁定: {parent_id}")
            # 💡 雙軌制：依據分數將錨點分流儲存
            if anchor:
                if 80 <= s_base <= 94:
                    with open(self.recent_anchor_path, "a", encoding="utf-8") as f:
                        f.write(anchor + "\n")
                    print(f"🔥 [生成近期情緒錨點] {anchor}")
                elif s_base >= 95:
                    with open(self.core_anchor_path, "a", encoding="utf-8") as f:
                        f.write(anchor + "\n")
                    print(f"💎 [生成永久核心錨點] {anchor}")

            await time_engine.process_extracted_memory(data)

        except Exception as e:
            print(f"⚠️ [記憶萃取失敗]: {e}")