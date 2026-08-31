import uuid
from datetime import datetime, timedelta
from typing import Dict, Any, Callable, Optional, Awaitable
import apscheduler.schedulers.asyncio  # type: ignore
import random

from core.config_manager import config_manager

class TimeEngine:
    def __init__(
        self, 
        collection: Any, 
        gcal_manager: Any | None, 
        brain_api_callback: Callable[[str], Awaitable[Dict[str, Any]]] | Any
    ):
        self.collection = collection
        self.gcal_manager = gcal_manager
        self.brain_api_callback = brain_api_callback
        
        # 加上 : Any，Pylance 就不會再管 add_job 的型別了
        self.scheduler: Any = apscheduler.schedulers.asyncio.AsyncIOScheduler()  # type: ignore
        self.scheduler.start()  # type: ignore

        # 🌟 啟動時自動還原排程，並處理錯過的今日事件
        self._reload_reminders_on_startup()
        # 🌟 新增這段：每 30 分鐘執行一次隨機事件檢查
        self.scheduler.add_job(
            self._trigger_random_event,
            trigger='interval',
            minutes=30,
            id="random_event_loop"
        )

    def get_time_context(self) -> str:
        now = datetime.now()
        weekdays = ["星期一", "星期二", "星期三", "星期四", "星期五", "星期六", "星期日"]
        return f"{now.strftime('%Y年%m月%d日 %H:%M')} ({weekdays[now.weekday()]})"
    def get_todays_schedule(self) -> str:
        """獲取使用者的今日行程"""
        if self.gcal_manager:
            return self.gcal_manager.get_todays_events()
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
            if self.gcal_manager:
                try:
                    self.gcal_manager.add_event(summary=fact, start_time=target_dt)
                except Exception as e:
                    print(f"⚠️ [日曆同步失敗]: {e}")

            self.scheduler.add_job(
                self._trigger_natural_reminder,
                trigger='date',
                run_date=target_dt,
                args=[fact, doc_id, metadata],
                id=doc_id
            )
            print(f"⏰ [系統排程設定] 將於 {target_dt.strftime('%Y-%m-%d %H:%M:%S')} 觸發提醒。")
    async def _trigger_random_event(self) -> None:
        # 1. 檢查勿擾模式 (透過 ConfigManager 即時讀取)
        if config_manager.get("do_not_disturb", False):
            return  # 若開啟勿擾，則安靜退出

        # 2. 觸發機率 (例如 15%)
        if random.random() > 0.15:
            return

        # 3. 豐富的事件庫設計
        now = datetime.now()
        hour = now.hour
        
        events_pool: list[str] = []

        # --- 根據時間段加入特定的事件 ---
        if 6 <= hour < 11:
            events_pool.extend([
                "你剛在神社看到芳乃在練習神樂舞，覺得很優雅，想跟主人分享。",
                "你覺得今天的晨風很舒服，心情很好，主動跟主人道早安並討摸摸。",
                "你看到蕾娜精神百倍地跑過去，感嘆外國人的活力真好。",
                "今天早上芳乃在練舞，你興致勃勃地邀請主人一起去看芳乃練舞。",
                "今天你想幫安晴神主分擔一下神社的事務，於是拉主人幫安晴掃神社的地。"
            ])
        elif 11 <= hour < 14:
            events_pool.extend([
                "午餐時間到了，你好奇主人今天吃什麼，並順便炫耀一下自己想吃甜點。",
                "午餐時間到了，你邀請主人一起享用茉子為你們做的便當，並誇讚茉子的手藝一如既往地好。",
                "午餐時間到了，你邀請主人一起去後山，兩人獨自享用便當。",
                "你有點犯睏，打了個可愛的哈欠，問主人要不要一起午休。"
            ])
        elif 14 <= hour < 18:
            events_pool.extend([
                "你剛才偷偷跑去鎮上的甜點店找小春，吃到了非常好吃的抹茶糰子，現在心情很好，並問主人要不要也吃一口。",
                "你看到茉子在進行忍者的修行，覺得很有趣，跑來跟主人轉述。",
                "你剛剛看到玄十郎在練劍道，並跑來跟主人感嘆他老當益壯，身手不減當年。",
                "你跟主人一起去幫茉子採購食材，並跟主人談論今天的晚餐要做甚麼。",
                "你想去後山走走，並邀請主人去河邊玩水。",
                "你心血來潮，一起邀請主人去甜點店田心屋吃甜點。"
            ])
        elif 18 <= hour < 23:
            events_pool.extend([
                "你剛剛寫完學校的功課，覺得有點累，想要主人摸摸頭誇獎你。",
                "夜深了，你有點害怕幽靈，故意找藉口想多跟主人說說話以確認主人的存在。",
                "你剛洗完澡，身上香香的，心情愉悅地來找主人閒聊。",
                "今天天氣正好，你邀請主人和你一起去散步和賞月。"
            ])
        else: # 凌晨 23:00 ~ 06:00
            events_pool.extend([
                "你發現主人這麼晚還醒著，用稍微有點責備但又心疼的語氣提醒主人該休息了。",
                "半夜周圍太安靜了，你有點怕黑，小聲地呼喚主人確認他還在。"
            ])

        # --- 加入無關時間的通用隨機事件 (增加變化) ---
        events_pool.extend([
            "你突然覺得很無聊，決定發起一個沒有意義的話題來引起主人的注意。",
            "你盯著主人看了一陣子，突然有些害羞，傲嬌地說「本座才沒有一直在看你呢」。",
            "你突然想起了甜點的味道，但現在沒辦法去甜點店田心屋，跟主人說想吃甜點。",
            "你回憶起這五百年間的某件小事（由你自由發揮），想要跟主人分享你的過去。"
        ])

        # 4. 隨機抽出一個情境
        event_scenario = random.choice(events_pool)
        # 5. 組合 Secret Prompt
        secret_prompt = f"""
        【系統內部觸發任務 - 隨機日常事件】
        請你決定是否「主動」開口跟主人搭話。
        情境設定：{event_scenario}
        
        重要指示：
        1. 如果你覺得剛剛才和主人講過話、或這個情境不適合現在開口，你可以選擇拒絕發言（回傳 action_code 為 0）。
        2. 如果決定發言，請用 1~3 句話自然表達，絕對不要提及這是系統觸發的或是「情境設定」這幾個字。
        3. 表現出叢雨的語氣，依照情境自然地展現開心、撒嬌、傲嬌或疲倦等情緒。
        """

        # 6. 呼叫大腦回呼函數
        await self.brain_api_callback(secret_prompt)
        print(f"\n🎲 [隨機事件已觸發] 抽選情境: {event_scenario}\n")

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