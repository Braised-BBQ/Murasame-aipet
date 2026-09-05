import uuid
from datetime import datetime, timedelta
from typing import Dict, Any, Callable, Optional, Awaitable
import apscheduler.schedulers.asyncio  # type: ignore
import random
import json # 👈 確認有 import json
import os   # 👈 確認有 import os
from core.weather import get_weather_async

from core.config_manager import config_manager

class TimeEngine:
    def __init__(
        self, 
        collection: Any, 
        brain_api_callback: Callable[[str], Awaitable[Dict[str, Any]]] | Any
    ):
        self.collection = collection
        self.brain_api_callback = brain_api_callback
        
        # 加上 : Any，Pylance 就不會再管 add_job 的型別了
        self.scheduler: Any = apscheduler.schedulers.asyncio.AsyncIOScheduler()  # type: ignore
        self.scheduler.start()  # type: ignore

        # 🌟 啟動時自動還原排程，並處理錯過的今日事件
        self._reload_reminders_on_startup()
        # 🌟 從 config 讀取觸發間隔，預設為 30 分鐘
        interval_minutes = config_manager.get("random_event_interval_minutes", 30)
        self.scheduler.add_job(
            self._trigger_random_event,
            trigger='interval',
            minutes=interval_minutes,
            id="random_event_loop"
        )
    def _get_recent_events(self) -> str:
        """讀取今天和昨天的隨機搭話紀錄，並自動清除過期記憶"""
        log_path = os.path.abspath(os.path.join(os.path.dirname(__file__), "../random_event_log.json"))
        if not os.path.exists(log_path):
            return "無"
            
        try:
            with open(log_path, "r", encoding="utf-8") as f:
                logs = json.load(f)
            
            now = datetime.now()
            yesterday = now - timedelta(days=1)
            
            # 👇 加上明確的型別標註
            recent_logs: list[str] = []
            valid_logs: list[Dict[str, Any]] = []
            for log in logs:
                log_time = datetime.strptime(log["time"], "%Y-%m-%d %H:%M:%S")
                # 只保留今天和昨天的紀錄
                if log_time.date() == now.date():
                    recent_logs.append(f"今天 {log_time.strftime('%H:%M')}：{log['scenario']}")
                    valid_logs.append(log)
                elif log_time.date() == yesterday.date():
                    recent_logs.append(f"昨天 {log_time.strftime('%H:%M')}：{log['scenario']}")
                    valid_logs.append(log)
                    
            # 順手將過期的紀錄刪除，覆寫回檔案，避免檔案無限變大
            with open(log_path, "w", encoding="utf-8") as f:
                json.dump(valid_logs, f, ensure_ascii=False, indent=2)
                
            return "\n".join(recent_logs) if recent_logs else "無"
        except Exception as e:
            print(f"⚠️ [讀取事件歷史失敗]: {e}")
            return "無"

    def _save_event_log(self, scenario: str) -> None:
        """將剛剛發生的搭話事件寫入日記"""
        log_path = os.path.abspath(os.path.join(os.path.dirname(__file__), "../random_event_log.json"))
        logs: list[Dict[str, Any]] = []
        if os.path.exists(log_path):
            try:
                with open(log_path, "r", encoding="utf-8") as f:
                    logs = json.load(f)
            except:
                pass
                
        logs.append({
            "time": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
            "scenario": scenario
        })
        
        try:
            with open(log_path, "w", encoding="utf-8") as f:
                json.dump(logs, f, ensure_ascii=False, indent=2)
        except Exception as e:
            print(f"⚠️ [儲存事件歷史失敗]: {e}")
    def update_random_event_interval(self) -> None:
        """提供給外部 (main.py) 呼叫，動態更新排程器時間"""
        new_interval = config_manager.get("random_event_interval_minutes", 30)
        try:
            self.scheduler.reschedule_job(
                "random_event_loop", 
                trigger='interval', 
                minutes=new_interval
            )
            print(f"🔄 [排程器更新] 隨機事件觸發間隔已熱修改為 {new_interval} 分鐘")
        except Exception as e:
            print(f"⚠️ [排程器更新失敗]: {e}")    

    def get_time_context(self) -> str:
        now = datetime.now()
        weekdays = ["星期一", "星期二", "星期三", "星期四", "星期五", "星期六", "星期日"]
        return f"{now.strftime('%Y年%m月%d日 %H:%M')} ({weekdays[now.weekday()]})"
    def get_todays_schedule(self) -> str:
        """獲取使用者的今日行程"""
        return ""
    def _reload_reminders_on_startup(self) -> None:
        """軟體啟動時，將資料庫中的排程重新載入，並處理錯過的全天/今日事件"""
        now = datetime.now()
        try:
            results = self.collection.get(where={"type": "reminder"})
            if not results or not results['metadatas']:
                return

            for i, meta in enumerate(results['metadatas']):
                event_time_str = str(meta.get("event_time_str", ""))
                fact = str(results['documents'][i])
                doc_id = str(results['ids'][i])

                if not event_time_str or event_time_str == "none":
                    continue

                target_dt = datetime.strptime(event_time_str, "%Y-%m-%d %H:%M:%S")

                # 情境 1：未來的事件 -> 正常排入日曆 (解決重啟軟體遺失排程的問題)
                if target_dt > now:
                    self.scheduler.add_job(
                        self._trigger_natural_reminder,
                        trigger='date',
                        run_date=target_dt,
                        args=[fact, doc_id, meta],
                        id=doc_id,
                        replace_existing=True
                    )
                    print(f"📅 [排程恢復] 已重新載入：{fact} ({event_time_str})")
                
                # 🌟 情境 2：是「今天」的事件，但時間已經過了 (如預設的早上 6:00，或軟體剛好沒開)
                elif target_dt.date() == now.date() and target_dt <= now:
                    # 安排在系統啟動 1 分鐘後提醒
                    run_date = now + timedelta(minutes=1)
                    self.scheduler.add_job(
                        self._trigger_natural_reminder,
                        trigger='date',
                        run_date=run_date,
                        args=[fact, doc_id, meta], 
                        id=f"missed_{doc_id}"
                    )
                    print(f"🔄 [啟動補償] 發現今日待辦：{fact}，將於 1 分鐘後提醒。")
                
                # 情境 3：昨天以前的事件 -> 已經完全錯過，直接降級為普通記憶，不再提醒
                else:
                    new_meta = meta.copy()
                    new_meta["type"] = "fact"
                    self.collection.update(ids=[doc_id], metadatas=[new_meta])

        except Exception as e:
            print(f"⚠️ [重載排程失敗]: {e}")

    async def process_extracted_memory(self, event_data: Dict[str, Any]) -> None:
        if not event_data.get("has_event"):
            return

        fact = str(event_data.get("fact", ""))
        time_str = event_data.get("event_time")
        is_future = bool(event_data.get("is_future_reminder", False))
        is_yearly = bool(event_data.get("is_yearly", False)) # 🌟 新增這行抓取年度標記
        if not fact or fact == "None":
            return

        target_dt: Optional[datetime] = None
        if time_str:
            try:
                target_dt = datetime.strptime(str(time_str), "%Y-%m-%d %H:%M:%S")
            except ValueError:
                pass

        doc_id = f"mem_{uuid.uuid4().hex[:8]}"
        metadata: Dict[str, Any] = {
            "created_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
            "event_time_str": str(time_str) if time_str else "none",
            "type": "reminder" if is_future else "fact",
            "is_yearly": is_yearly # 🌟 新增這行把標記存進資料庫
        }
        
        self.collection.add(documents=[fact], metadatas=[metadata], ids=[doc_id])
        print(f"✅ [長期記憶儲存] {fact} (時間: {time_str})")

        if is_future and target_dt and target_dt > datetime.now():

            self.scheduler.add_job(
                self._trigger_natural_reminder,
                trigger='date',
                run_date=target_dt,
                args=[fact, doc_id, metadata],
                id=doc_id
            )
            print(f"⏰ [系統排程設定] 將於 {target_dt.strftime('%Y-%m-%d %H:%M:%S')} 觸發提醒。")
    async def _trigger_random_event(self) -> None:
        # 1. 檢查勿擾模式
        if config_manager.get("do_not_disturb", False):
            return 

        # 2. 觸發機率 (從 config 讀取，預設 15%)
        trigger_prob = config_manager.get("random_event_probability", 0.15)
        if random.random() > trigger_prob:
            return

       # 🌟 3. 取得超詳細天氣資訊 (直接呼叫我們做好的獨立模組)
        weather_info = await get_weather_async()
        # 4. 根據時間動態組合【時間限定靈感】 (將原本的事件庫精煉成提示詞)
        now = datetime.now()
        hour = now.hour
        
        if 6 <= hour < 11:
            time_context = "早上：可以分享看到芳乃練神樂舞、蕾娜充滿活力、幫安晴掃地，或是讚美晨風很舒服、主動討摸摸。"
        elif 11 <= hour < 14:
            time_context = "中午：可以好奇主人的午餐、炫耀想吃甜點、邀請享用茉子的便當、提議去後山野餐，或是犯睏想午休。"
        elif 14 <= hour < 18:
            time_context = "下午：可以分享去田心屋找小春吃抹茶糰子、看茉子練忍術、看玄十郎練劍道、邀請主人一起去幫忙採買食材，或邀請主人去河邊玩水。"
        elif 18 <= hour < 23:
            time_context = "晚上：可以說剛寫完功課討摸摸、洗完澡香香的來閒聊、因為怕幽靈找藉口跟主人說話，或是邀請散步賞月。"
        else: # 凌晨 23:00 ~ 06:00
            time_context = "深夜：可以用責備又心疼的語氣提醒熬夜的主人休息，或是因為怕黑/怕安靜而小聲呼喚主人。"
        # 👇 [新增] 讀取近期記憶
        recent_history = self._get_recent_events()
        # 5. 組合 Secret Prompt
        secret_prompt = f"""
        【系統內部觸發任務 - 動態隨機搭話】
        當前時間：{now.strftime('%H:%M')}
        【系統環境數據】
        {weather_info}
        【近期搭話歷史紀錄】 (這是你今天和昨天已經主動開口聊過的事情)
        {recent_history}
        任務指示：
        請你（叢雨）自行發想一個「日常小情境」來主動跟主人搭話。
        為了確保話題豐富，請從以下【話題靈感】中隨機挑選 1 個方向發揮（不要照抄，用你自己的話說）：

        - 方向 A（時間限定）：結合目前的時段特徵（{time_context}），分享見聞或邀請主人。
        - 方向 B（天氣與天文）：強烈結合現在的天氣或月相發揮（例如約主人賞月、天氣熱提醒喝水、下雨慶幸待在室內）。
        - 方向 C（泛用日常與撒嬌）：
          1. 覺得無聊，發起沒有意義的話題。
          2. 盯著主人看被發現，傲嬌地否認。
          3. 突然想念田心屋甜點的味道。
          4. 回憶起這五百年間的某件小事跟主人分享。
        - 方向 D（關心陪伴）：看到主人一直盯著螢幕（工作、寫作業、玩遊戲），給予關心、提醒休息或鼓勵。

        【天氣數據運用規則 - 非常重要】：
        1.絕對不要像氣象播報員一樣條列式唸出環境數據或具體數值！
        2.請將天氣資訊視為「背景條件」，用來輔助你的情境搭話，或據此判斷當下是否適合發言。
        3.對話的核心必須放在情境本身（如撒嬌、邀約、關心），天氣只是順帶一提的理由。例如：「今天出太陽了，我們去後山散步吧」，而不是「今天氣溫 28 度，降雨機率 0%，適合去散步」。

        嚴格要求：
        1. 【絕對不要】在回覆中提及「方向A」、「系統觸發」、「根據情境設定」等破壞沉浸感的詞彙。
        2. 【話題冷卻與記憶機制】請觀察歷史紀錄中的「時間」：
           - 2 小時內的相同話題（例如剛聊過下雨、剛吃過甜點）：請絕對避免，強制選擇其他完全不同的方向。
           - 超過 4 小時或昨天的紀錄：可以再次聊同類別，但必須展現出「時間流逝的連貫性」。例如：「昨天吃了糰子，今天想換換口味」、「早上的雨終於停了呢」，絕對不能像第一次發生那樣說話。
           - 若是同一個話題的緊密延續，請表現出「接續對話」的語氣（例如：「就像剛剛提到的...」）。
        3. 【絕對不要】提及「根據情境設定」、「歷史紀錄」等破壞沉浸感的詞彙。
        4. 請用 1~3 句話自然表達，展現你溫柔、愛撒嬌或偶爾小傲嬌的性格。
        5. 如果你覺得當下的時間太晚，或者天氣狀況不適合搭話，你可以選擇沉默（回傳 action_code: 0）。
        """

        # 6. 呼叫大腦回呼函數，並接收回傳結果
        # ✅ 修正 1：變數名稱統一改為 spoken_text
        spoken_text = await self.brain_api_callback(secret_prompt)
        
        if spoken_text: 
            # ✅ 修正 2：移除不存在的 chosen_scenario，直接記錄大腦實際發言
            log_content = f"實際發言：「{spoken_text}」"
            self._save_event_log(log_content)
            print(f"\n🎲 [隨機事件成功] 已將實際發言寫入近期記憶: {spoken_text}\n")
        else:
            print(f"\n🎲 [隨機事件略過] 大腦選擇了沉默。\n")

    # 🌟 修改此函數：加入 doc_id 與 meta，提醒完自動標記為已完成
    async def _trigger_natural_reminder(self, fact: str, doc_id: str, meta: Dict[str, Any]) -> None:
        now_str = datetime.now().strftime("%m月%d日 %H:%M")
        
        secret_prompt = f"""
        【系統內部觸發任務 - 時間已到】
        當前時間：{now_str}
        原本排定的事件內容是：「{fact}」
        
        重要指示：
        1. 預定要執行的時間點已經到了！
        2. 請將事件內容轉化為「現在該做了」或「時間到了」的語氣，絕對不要再說「幾分鐘後」或「等一下」。
        3. 請以你（叢雨）的人設與口吻，自然地發起對話來提醒主人。
        """

        await self.brain_api_callback(secret_prompt)
        print(f"\n🔔 [系統觸發訊號已發送]\n")

        # 🌟 提醒完成後，更新 ChromaDB，把 type 從 reminder 降級成 fact，避免重啟又再提醒
        try:
            new_meta = meta.copy()
            new_meta["type"] = "fact"
            self.collection.update(ids=[doc_id], metadatas=[new_meta])
        except Exception as e:
            print(f"⚠️ [更新記憶狀態失敗]: {e}")