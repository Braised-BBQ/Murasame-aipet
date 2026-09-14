import os
import json
import uuid
import chromadb
from typing import Any, cast
import math
import sqlite3
from openai import AsyncOpenAI
from datetime import datetime, timedelta
import re  # 🌟 新增這行：正則表示式模組

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
    def _extract_time_intent(self, query: str) -> tuple[datetime | None, int]:
        """
        解析對話中的時間意圖，回傳 (目標時間, 模糊窗口天數)
        窗口天數 (sigma) 決定了高斯分佈的寬度。單位越大，加分範圍越廣。
        """
        now = datetime.now()
        
        # 加上明確的 dict[str, float] 型別提示，並統一數值格式
        num_map: dict[str, float] = {
            '一': 1.0, '二': 2.0, '兩': 2.0, '三': 3.0, '四': 4.0, '五': 5.0, 
            '六': 6.0, '七': 7.0, '八': 8.0, '九': 9.0, '十': 10.0, '半': 0.5
        }
        
        def parse_num(s: str) -> float:
            return float(s) if s.isdigit() else float(num_map.get(s, 1.0))

        # 0. 解析「幾年前」(極寬鬆的高斯曲線，涵蓋一整年，sigma=180天)
        m = re.search(r'(\d+|一|二|兩|三|四|五|六|七|八|九|十)年前', query)
        if m:
            years = parse_num(m.group(1))
            return now - timedelta(days=int(years * 365)), 180

        # 1. 解析「幾個月前」(寬鬆的高斯曲線，涵蓋約一到兩個月，sigma=30天)
        m = re.search(r'(\d+|一|二|兩|三|四|五|六|七|八|九|十|半)個?月前', query)
        if m:
            months = parse_num(m.group(1))
            months = 6 if months == 0.5 else months
            return now - timedelta(days=int(months * 30)), 30

        # 2. 解析「幾週前」(中等的高斯曲線，sigma=7天)
        m = re.search(r'(\d+|一|二|兩|三|四|五|六|七|八|九|十)個?(週|禮拜)前', query)
        if m:
            weeks = parse_num(m.group(1))
            return now - timedelta(days=int(weeks * 7)), 7
            
        # 3. 解析「幾天前」(嚴格的高斯曲線，sigma=2天)
        m = re.search(r'(\d+|一|二|兩|三|四|五|六|七|八|九|十)天前', query)
        if m:
            days = parse_num(m.group(1))
            return now - timedelta(days=int(days)), 2

        # 4. 常用口語 (同步放大去年與前年的窗口)
        if "前年" in query: return now - timedelta(days=730), 180
        if "去年" in query: return now - timedelta(days=365), 180
        if "上週" in query or "上個禮拜" in query: return now - timedelta(days=7), 7
        if "昨天" in query: return now - timedelta(days=1), 2
        if "前天" in query: return now - timedelta(days=2), 2

        return None, 0
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
        
        # 🌟 1. 先掃描這句話有沒有隱含「時間查詢意圖」
        target_date, window_days = self._extract_time_intent(query)
        if target_date:
            print(f"⏱️ [時間意圖捕捉] 目標時間: {target_date.strftime('%Y-%m-%d')} | 模糊半徑: ±{window_days}天")
        
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
            
            created_at_str = str(meta.get("created_at", now.strftime("%Y-%m-%d %H:%M:%S")))
            created_at = datetime.strptime(created_at_str, "%Y-%m-%d %H:%M:%S")
            delta_days: int = max(0, (now - created_at).days)
            
            s_base = int(meta.get("s_base", 50))
            recall_count = int(meta.get("recall_count", 0))
            
            if s_base >= 95:# 永久核心記憶，完全不衰退
                v_t: float = 1.0
            else:# 依據 S_base 與 recall_count 計算衰退係數
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
            
            # 🌟 3. 計算高斯時間加權 (Gaussian Temporal Bonus)
            t_bonus = 0.0
            if target_date and window_days > 0:
                # 計算記憶發生日與目標日的「天數落差」
                diff_days = abs((created_at - target_date).days)
                
                # 使用常態分佈公式：落差越小，得分越接近 0.3；落差超過 window_days 則分數趨近於 0
                t_bonus = 0.3 * math.exp(- (diff_days ** 2) / (2 * (window_days ** 2)))

            # 讓高相似度依然很高，低相似度瞬間掉下去，最後「加上」時間加權分
            sharpened_sim = sim ** 2 
            w_m: float = ((0.7 * sharpened_sim + 0.3 * (n_tags / 10.0)) * v_t * p_c) + t_bonus
            
            print(f"🧠 [評估] {doc_id[-6:]} | Sim: {sim:.2f} | V_t: {v_t:.2f} | T_bonus: {t_bonus:.3f} => W: {w_m:.3f}")

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
                
                # ==========================================
                # 🔗 核心新增：因果鏈追溯喚醒 (順藤摸瓜)
                # ==========================================
                parent_id = best_meta.get("parent_id")
                if parent_id and parent_id != "none" and parent_id != "null":
                    try:
                        parent_result = self.collection.get(ids=[str(parent_id)])
                        
                        # 🌟 明確抽出變數並進行 None 檢查，安撫 Pylance
                        p_docs = parent_result.get("documents")
                        p_metas = parent_result.get("metadatas")
                        
                        if p_docs is not None and len(p_docs) > 0 and p_metas is not None and len(p_metas) > 0:
                            # 🌟 強制轉型，確保型別安全
                            parent_doc = str(p_docs[0])
                            p_meta_raw = p_metas[0]
                            parent_meta = cast(dict[str, Any], p_meta_raw) if isinstance(p_meta_raw, dict) else {}
                            parent_time = str(parent_meta.get("created_at", "過去"))
                            
                            best_memory_str += (
                                f"\n\n【🔗 記憶深處的因果聯想】\n"
                                f"這件事似乎與之前發生的這件事有直接關聯：\n"
                                f"時間：{parent_time}\n"
                                f"關聯事件：{parent_doc}\n"
                            )
                            print(f"🔗 [因果鏈喚醒] 成功串聯過去記憶: {parent_doc}")
                    except Exception as e:
                        print(f"⚠️ [因果鏈溯源失敗]: {e}")

            except Exception as e:
                print(f"⚠️ [記憶鞏固失敗]: {e}")

        return best_memory_str

    async def extract_and_save_memory(self, user_text: str, time_engine: TimeEngine) -> None:
        raw_key = config_manager.get("openai_api_key", config_manager.get("api_key", ""))
        api_key = raw_key if raw_key else "sk-dummy-key"
        client = AsyncOpenAI(api_key=api_key, base_url=config_manager.get("base_url", None))
        model_name = str(config_manager.get("model", "gpt-4o-mini"))
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
            請評估主人剛才說的話：「{user_text}」
            
            【S_base 記憶深度打分指南 (多維度綜合評估)】
            請綜合評估「資訊價值」、「情感強烈度」與「對未來關係的影響力」，給出 0-100 的 S_base：
            - [0-29] 毫無記憶價值：無意義的語氣詞、隨機亂碼 (例如：「嗯」、「好」)。
            - [30-49] 日常短期記憶：吃喝拉撒、例行公事、低情緒波動 (例如：「我今天吃麵」、「去開會」)。
            - [50-79] 中期重要記憶：具體事件、明確的情緒起伏 (例如：「剛修完一個超難的 Bug，快累死了」)。
            - [80-94] 長期深刻記憶：強烈的情感依賴、興趣喜好、共同經歷的重要回憶、深刻的約定 、長期的習慣(例如：「之前我搞砸時多虧有妳陪我，週末一起去吃蛋糕吧」)。
            - [95-100] 永久核心記憶：人生重大轉折、極度強烈的情感羈絆、對系統或彼此關係的底層定義、重要的紀念日。

            🔥 ⚠️ 【自述錨點 (anchor) 撰寫絕對守則】⚠️ 🔥
            當 S_base >= 80 時，你就是「叢雨本人」來寫這段內心獨白（anchor），這將成為她的靈魂記憶：
            1. 人設綁定：自稱必須是「本座」，稱呼對方為「主人」。
            2. 性格特質：稍微有點傲嬌但同時你喜歡甜食、撒嬌。表面上有時會吐槽主人，但內心深處其實非常高興、心疼且依賴主人。
            3. 絕對禁止機器感：嚴禁出現「核心模組」、「處理單元」、「數據」、「程式」等詞彙。本座是有靈魂的少女！
            
            【標籤數量限制規則 (嚴格執行)】
            - S_base 在同區間內分數越高，盡可能打越多標籤。
            - S_base < 50：最多打 2 個標籤。
            - S_base 50~79：可打 3~5 個標籤。
            - S_base >= 80：可打 6~10 個標籤。

            【🔗 因果鏈綁定 (parent_id)】
            系統剛才在腦海中閃過了這段舊記憶：{potential_parent_str}
            如果主人現在的話，明顯是這段舊記憶的「後續結果」、「起因」或「強烈關聯」，請在 JSON 的 parent_id 欄位填入該 ID。
            如果毫無關聯，請務必填入 null。

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
                "is_future_reminder": true 或 false (只有在需要未來特定時間點主動提醒時，才設為 true),
                "is_yearly": true 或 false (如果是每年固定發生的日子，例如生日、紀念日，請設為 true)
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