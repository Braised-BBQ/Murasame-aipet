import datetime
import os
from typing import Any, Dict, List

from googleapiclient.discovery import build  # type: ignore
from google_auth_oauthlib.flow import InstalledAppFlow  # type: ignore

class GoogleCalendarManager:
    SCOPES = ['https://www.googleapis.com/auth/calendar.events']

    def __init__(self, credentials_path: str):
        if not os.path.exists(credentials_path):
            raise FileNotFoundError(f"找不到 Google 日曆憑證檔案：{credentials_path}")
            
        flow = InstalledAppFlow.from_client_secrets_file(credentials_path, self.SCOPES)  # type: ignore
        creds = flow.run_local_server(port=0)  # type: ignore
        self.service: Any = build('calendar', 'v3', credentials=creds)  # type: ignore

    def add_event(self, summary: str, start_time: datetime.datetime, minutes_duration: int = 60) -> str:
        """將事件寫入 Google 日曆"""
        try:
            end_time = start_time + datetime.timedelta(minutes=minutes_duration)
            
            event: Dict[str, Any] = {
                'summary': summary,
                'description': '由 AI 助理自動排程的提醒',
                'start': {
                    'dateTime': start_time.isoformat(),
                    'timeZone': 'Asia/Taipei',
                },
                'end': {
                    'dateTime': end_time.isoformat(),
                    'timeZone': 'Asia/Taipei',
                },
                'reminders': {
                    'useDefault': False,
                    'overrides': [
                        {'method': 'popup', 'minutes': 10},
                    ],
                },
            }

            # 🌟 明確告訴 Pylance，created_event 是一個字典
            created_event: Dict[str, Any] = self.service.events().insert(calendarId='primary', body=event).execute()  # type: ignore
            return str(created_event.get('id', ''))
            
        except Exception as e:
            print(f"⚠️ [寫入日曆失敗]: {e}")
            return "" 

    def get_todays_events(self) -> str:
        """取得今日剩餘的行程"""
        try:
            now = datetime.datetime.now().astimezone()
            end_of_day = now.replace(hour=23, minute=59, second=59)

            # 🌟 明確告訴 Pylance，API 回傳的總結果是一個字典
            events_result: Dict[str, Any] = self.service.events().list(  # type: ignore
                calendarId='primary', 
                timeMin=now.isoformat(),
                timeMax=end_of_day.isoformat(),
                maxResults=10, 
                singleEvents=True,
                orderBy='startTime'
            ).execute()  # type: ignore
            
            # 🌟 明確告訴 Pylance，events 是一個裝滿字典的陣列
            events: List[Dict[str, Any]] = events_result.get('items', [])  # type: ignore
            
            if not events:
                return ""

            schedule_list: List[str] = []
            for evt in events:
                # 🌟 把迴圈內的每一個項目，都精確標註為字典
                event_data: Dict[str, Any] = evt 
                
                # 🌟 針對嵌套的 start 屬性，也獨立抽出來標註為字典
                start_dict: Dict[str, Any] = event_data.get('start', {})  # type: ignore
                
                # 現在 Pylance 知道大家都是字典了，就不會再該該叫了
                start = str(start_dict.get('dateTime', start_dict.get('date')))
                summary = str(event_data.get('summary', '未命名行程'))
                
                if 'T' in start:
                    time_str = start[11:16]
                else:
                    time_str = "全天"
                    
                schedule_list.append(f"- {time_str}: {summary}")
            
            return "【今日後續的 Google 日曆行程】：\n" + "\n".join(schedule_list)
            
        except Exception as e:
            print(f"⚠️ [日曆讀取失敗]: {e}")
            return ""