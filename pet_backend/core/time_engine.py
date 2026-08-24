import uuid
from datetime import datetime, timedelta
from typing import Dict, Any, Callable, Optional, Awaitable
import apscheduler.schedulers.asyncio  # type: ignore

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