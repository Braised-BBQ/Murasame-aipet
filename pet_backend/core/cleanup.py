import sqlite3
import chromadb
from typing import cast, Any

def run_half_year_cleanup() -> None:
    conn = sqlite3.connect("chat_history.db")
    cursor = conn.cursor()
    
    client = chromadb.PersistentClient(path="./chroma_db")
    collection = client.get_collection(name="murasame_memory")
    
    # 1. 找出所有高分記憶 (S_base >= 80)
    high_score_docs = collection.get(where={"s_base": {"$gte": 80}})
    
    # 💡 嚴格宣告這是一個裝有整數的 Set
    protected_ids: set[int] = set()
    
    # 2. 框出保護區 (重要事件前後各 15 句對話)
    metadatas = high_score_docs.get("metadatas")
    
    if metadatas:
        for meta_raw in metadatas:
            # 💡 改用 isinstance 判斷是否為字典，安撫 Pylance 同時過濾異常值
            if isinstance(meta_raw, dict):
                meta = cast(dict[str, Any], meta_raw)
                sql_id_raw = meta.get("sqlite_id")
                
                if sql_id_raw is not None:
                    sql_id = int(str(sql_id_raw))
                    for i in range(sql_id - 15, sql_id + 16):
                        protected_ids.add(i)
                    
    # 3. 刪除半年前，且「不在保護區內」的流水帳
    if protected_ids:
        # 有保護區時的刪除邏輯
        seq = ','.join(['?'] * len(protected_ids))
        cursor.execute(f"""
            DELETE FROM logs 
            WHERE timestamp < date('now', '-6 months') 
            AND id NOT IN ({seq})
        """, list(protected_ids))
    else:
        # 若系統內完全沒有任何高分記憶，直接刪除半年前所有資料
        cursor.execute("""
            DELETE FROM logs 
            WHERE timestamp < date('now', '-6 months')
        """)
    
    deleted_count = cursor.rowcount
    conn.commit()
    conn.close()
    
    print(f"🧹 [空間釋放完成] 已刪除 {deleted_count} 筆無用流水帳，完美保留高分記憶上下文。")

if __name__ == "__main__":
    run_half_year_cleanup()