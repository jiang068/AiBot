import json
import os
import sqlite3
from typing import Any, Dict, List, Optional

_db_path = None
_db = None


def __create_database_instance():
    global _db_path
    _db_path = os.getenv("SQLITE_DB_PATH", "data/aibot.db")
    os.makedirs(os.path.dirname(_db_path), exist_ok=True)
    return sqlite3.connect(_db_path, check_same_thread=False)


def get_db():
    """获取数据库连接实例，延迟初始化。"""
    global _db
    if _db is None:
        _db = __create_database_instance()
        _db.row_factory = sqlite3.Row  # 使结果可通过列名访问
        __init_tables()
    return _db


def __init_tables():
    """初始化数据库表"""
    db = get_db()
    cursor = db.cursor()
    
    # messages表
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS messages (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            message_id TEXT,
            time REAL,
            chat_id TEXT,
            chat_info TEXT,
            user_info TEXT,
            processed_plain_text TEXT,
            detailed_plain_text TEXT,
            topic TEXT,
            memorized_times INTEGER DEFAULT 0,
            group_id TEXT,
            memorized INTEGER DEFAULT 0
        )
    ''')
    
    # recalled_messages表
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS recalled_messages (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            message_id TEXT,
            time REAL,
            stream_id TEXT
        )
    ''')
    
    # graph_nodes表
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS graph_nodes (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            concept TEXT UNIQUE,
            memory_items TEXT,
            created_time REAL,
            last_modified REAL
        )
    ''')
    
    # graph_edges表
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS graph_edges (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            source TEXT,
            target TEXT,
            strength INTEGER DEFAULT 1,
            created_time REAL,
            last_modified REAL,
            hash TEXT,
            UNIQUE(source, target)
        )
    ''')
    
    # llm_usage表
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS llm_usage (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            timestamp REAL,
            model_name TEXT,
            user_id TEXT,
            request_type TEXT,
            input_tokens INTEGER,
            output_tokens INTEGER,
            total_tokens INTEGER,
            cost REAL
        )
    ''')
    
    # chat_streams表
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS chat_streams (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            stream_id TEXT UNIQUE,
            platform TEXT,
            user_info TEXT,
            group_info TEXT,
            last_message_time REAL,
            created_time REAL,
            message_count INTEGER DEFAULT 0
        )
    ''')
    
    # schedule表
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS schedule (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            date TEXT UNIQUE,
            schedule TEXT
        )
    ''')
    
    # images表
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS images (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            hash TEXT,
            type TEXT,
            url TEXT,
            path TEXT,
            UNIQUE(hash, type)
        )
    ''')
    
    # image_descriptions表
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS image_descriptions (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            hash TEXT,
            type TEXT,
            description TEXT,
            UNIQUE(hash, type)
        )
    ''')
    
    # relationships表
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS relationships (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id TEXT,
            platform TEXT,
            nickname TEXT,
            relationship_value REAL,
            gender TEXT,
            age INTEGER,
            saved INTEGER DEFAULT 0,
            UNIQUE(user_id, platform)
        )
    ''')
    
    # knowledges表
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS knowledges (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            content TEXT,
            embedding TEXT,
            metadata TEXT
        )
    ''')
    
    # reasoning_logs表
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS reasoning_logs (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            timestamp REAL,
            user_input TEXT,
            reasoning_process TEXT,
            final_output TEXT,
            metadata TEXT
        )
    ''')
    
    # emoji表
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS emoji (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            filename TEXT UNIQUE,
            path TEXT,
            hash TEXT,
            embedding TEXT,
            discription TEXT,
            usage_count INTEGER DEFAULT 0,
            timestamp INTEGER DEFAULT 0
        )
    ''')
    # 兼容旧表：尝试添加缺失列（若列已存在会报错，忽略即可）
    for _col, _def in [
        ("path", "TEXT"),
        ("hash", "TEXT"),
        ("discription", "TEXT"),
        ("timestamp", "INTEGER DEFAULT 0"),
    ]:
        try:
            cursor.execute(f"ALTER TABLE emoji ADD COLUMN {_col} {_def}")
        except Exception:
            pass
    
    # store_memory_dots表
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS store_memory_dots (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            data TEXT
        )
    ''')
    
    db.commit()


class DBWrapper:
    """数据库代理类，保持接口兼容性同时实现懒加载。"""

    def __getattr__(self, name):
        if name == "graph_data":
            return GraphDataCollection()
        return DBCollection(name)


class GraphDataCollection:
    """处理graph_data子集合的类"""
    
    def __init__(self):
        self.db = get_db()
    
    def delete_many(self, query: Dict[str, Any]) -> None:
        """删除所有图数据"""
        cursor = self.db.cursor()
        cursor.execute("DELETE FROM graph_nodes")
        cursor.execute("DELETE FROM graph_edges")
        self.db.commit()
    
    @property
    def nodes(self):
        return DBCollection("graph_data.nodes")
    
    @property
    def edges(self):
        return DBCollection("graph_data.edges")


class DBCollection:
    """模拟MongoDB集合的类"""
    
    def __init__(self, name: str):
        self.name = name
        self.db = get_db()
    
    def insert_one(self, document: Dict[str, Any]) -> None:
        """插入单个文档"""
        cursor = self.db.cursor()
        if self.name == "messages":
            cursor.execute('''
                INSERT INTO messages 
                (message_id, time, chat_id, chat_info, user_info, processed_plain_text, 
                 detailed_plain_text, topic, memorized_times, group_id, memorized)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ''', (
                document.get("message_id"),
                document.get("time"),
                document.get("chat_id"),
                str(document.get("chat_info", {})),
                str(document.get("user_info", {})),
                document.get("processed_plain_text"),
                document.get("detailed_plain_text"),
                document.get("topic"),
                document.get("memorized_times", 0),
                document.get("group_id"),
                document.get("memorized", 0)
            ))
        elif self.name == "recalled_messages":
            cursor.execute('''
                INSERT INTO recalled_messages (message_id, time, stream_id)
                VALUES (?, ?, ?)
            ''', (
                document.get("message_id"),
                document.get("time"),
                document.get("stream_id")
            ))
        elif self.name == "llm_usage":
            cursor.execute('''
                INSERT INTO llm_usage 
                (timestamp, model_name, user_id, request_type, input_tokens, output_tokens, total_tokens, cost)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            ''', (
                document.get("timestamp"),
                document.get("model_name"),
                document.get("user_id"),
                document.get("request_type"),
                document.get("input_tokens"),
                document.get("output_tokens"),
                document.get("total_tokens"),
                document.get("cost")
            ))
        elif self.name == "schedule":
            cursor.execute('''
                INSERT INTO schedule (date, schedule)
                VALUES (?, ?)
            ''', (
                document.get("date"),
                document.get("schedule")
            ))
        elif self.name == "images":
            cursor.execute('''
                INSERT OR REPLACE INTO images (hash, type, url, path)
                VALUES (?, ?, ?, ?)
            ''', (
                document.get("hash"),
                document.get("type"),
                document.get("url"),
                document.get("path")
            ))
        elif self.name == "image_descriptions":
            cursor.execute('''
                INSERT OR REPLACE INTO image_descriptions (hash, type, description)
                VALUES (?, ?, ?)
            ''', (
                document.get("hash"),
                document.get("type"),
                document.get("description")
            ))
        elif self.name == "relationships":
            cursor.execute('''
                INSERT OR REPLACE INTO relationships 
                (user_id, platform, nickname, relationship_value, gender, age, saved)
                VALUES (?, ?, ?, ?, ?, ?, ?)
            ''', (
                document.get("user_id"),
                document.get("platform"),
                document.get("nickname"),
                document.get("relationship_value"),
                document.get("gender"),
                document.get("age"),
                1 if document.get("saved", False) else 0
            ))
        elif self.name == "reasoning_logs":
            cursor.execute('''
                INSERT INTO reasoning_logs 
                (timestamp, user_input, reasoning_process, final_output, metadata)
                VALUES (?, ?, ?, ?, ?)
            ''', (
                document.get("timestamp"),
                document.get("user_input"),
                document.get("reasoning_process"),
                document.get("final_output"),
                str(document.get("metadata", {}))
            ))
        elif self.name == "emoji":
            cursor.execute('''
                INSERT OR IGNORE INTO emoji (filename, path, hash, embedding, discription, usage_count, timestamp)
                VALUES (?, ?, ?, ?, ?, ?, ?)
            ''', (
                document.get("filename"),
                document.get("path"),
                document.get("hash"),
                str(document.get("embedding", [])),
                document.get("discription"),
                document.get("usage_count", 0),
                document.get("timestamp", 0),
            ))
        elif self.name == "store_memory_dots":
            cursor.execute('''
                INSERT INTO store_memory_dots (data)
                VALUES (?)
            ''', (str(document),))
        elif self.name == "graph_data.nodes":
            cursor.execute('''
                INSERT OR REPLACE INTO graph_nodes 
                (concept, memory_items, created_time, last_modified)
                VALUES (?, ?, ?, ?)
            ''', (
                document.get("concept"),
                str(document.get("memory_items", [])),
                document.get("created_time"),
                document.get("last_modified")
            ))
        elif self.name == "graph_data.edges":
            cursor.execute('''
                INSERT OR REPLACE INTO graph_edges 
                (source, target, strength, created_time, last_modified, hash)
                VALUES (?, ?, ?, ?, ?, ?)
            ''', (
                document.get("source"),
                document.get("target"),
                document.get("strength", 1),
                document.get("created_time"),
                document.get("last_modified"),
                document.get("hash")
            ))
        elif self.name == "knowledges":
            cursor.execute('''
                INSERT INTO knowledges (content, embedding, metadata)
                VALUES (?, ?, ?)
            ''', (
                document.get("content"),
                str(document.get("embedding", [])),
                str(document.get("metadata", {}))
            ))
        self.db.commit()
    
    def find_one(self, query: Dict[str, Any] = None, sort: List = None) -> Optional[Dict[str, Any]]:
        """查找单个文档"""
        cursor = self.db.cursor()
        if query is None:
            query = {}
        
        if self.name == "messages":
            sql = "SELECT * FROM messages"
            params = []
            where_clauses = []
            
            if "message_id" in query:
                where_clauses.append("message_id = ?")
                params.append(query["message_id"])
            
            if "time" in query:
                if "$lte" in query["time"]:
                    where_clauses.append("time <= ?")
                    params.append(query["time"]["$lte"])
                elif "$gt" in query["time"]:
                    where_clauses.append("time > ?")
                    params.append(query["time"]["$gt"])
            
            if "group_id" in query:
                where_clauses.append("group_id = ?")
                params.append(query["group_id"])
            
            if "chat_id" in query:
                where_clauses.append("chat_id = ?")
                params.append(query["chat_id"])
            
            if where_clauses:
                sql += " WHERE " + " AND ".join(where_clauses)
            
            if sort:
                if isinstance(sort, dict):
                    sort_items = list(sort.items())
                else:
                    sort_items = sort
                for sort_field, direction in sort_items:
                    sql += f" ORDER BY {sort_field} {'DESC' if direction == -1 else 'ASC'}"
                    break
            else:
                sql += " ORDER BY id DESC"
            
            sql += " LIMIT 1"
            cursor.execute(sql, params)
            row = cursor.fetchone()
            if row:
                r = dict(row)
                r.setdefault("_id", r.get("id"))  # 兼容 MongoDB 风格的 _id 访问
                return r
        
        elif self.name == "schedule":
            sql = "SELECT * FROM schedule WHERE date = ?"
            cursor.execute(sql, (query.get("date"),))
            row = cursor.fetchone()
            if row:
                return dict(row)
        
        elif self.name == "chat_streams":
            sql = "SELECT * FROM chat_streams WHERE stream_id = ?"
            cursor.execute(sql, (query.get("stream_id"),))
            row = cursor.fetchone()
            if row:
                return dict(row)
        
        elif self.name == "images":
            sql = "SELECT * FROM images"
            params = []
            where_clauses = []
            if "hash" in query:
                where_clauses.append("hash = ?")
                params.append(query["hash"])
            if "type" in query:
                where_clauses.append("type = ?")
                params.append(query["type"])
            if where_clauses:
                sql += " WHERE " + " AND ".join(where_clauses)
            sql += " LIMIT 1"
            cursor.execute(sql, params)
            row = cursor.fetchone()
            if row:
                return dict(row)
        
        elif self.name in ["graph_data.nodes", "graph_nodes"]:
            sql = "SELECT * FROM graph_nodes WHERE concept = ?"
            cursor.execute(sql, (query.get("concept"),))
            row = cursor.fetchone()
            if row:
                return dict(row)
        
        elif self.name == "emoji":
            sql = "SELECT * FROM emoji"
            params = []
            where_clauses = []
            if "hash" in query:
                where_clauses.append("hash = ?")
                params.append(query["hash"])
            if "filename" in query:
                where_clauses.append("filename = ?")
                params.append(query["filename"])
            if "_id" in query or "id" in query:
                where_clauses.append("id = ?")
                params.append(query.get("_id") or query.get("id"))
            if where_clauses:
                sql += " WHERE " + " AND ".join(where_clauses)
            sql += " LIMIT 1"
            cursor.execute(sql, params)
            row = cursor.fetchone()
            if row:
                r = dict(row)
                try:
                    import ast
                    r["embedding"] = ast.literal_eval(r["embedding"]) if r.get("embedding") else []
                except Exception:
                    r["embedding"] = []
                return r
        
        return None
    
    def find(self, query: Dict[str, Any] = None, sort: List = None, limit: int = None):
        """查找多个文档"""
        cursor = self.db.cursor()
        if query is None:
            query = {}
        
        if self.name == "messages":
            sql = "SELECT * FROM messages"
            params = []
            where_clauses = []
            
            if "time" in query:
                if "$lte" in query["time"]:
                    where_clauses.append("time <= ?")
                    params.append(query["time"]["$lte"])
                elif "$gt" in query["time"]:
                    where_clauses.append("time > ?")
                    params.append(query["time"]["$gt"])
            
            if "group_id" in query:
                where_clauses.append("group_id = ?")
                params.append(query["group_id"])
            
            if "chat_id" in query:
                where_clauses.append("chat_id = ?")
                params.append(query["chat_id"])
            
            if "message_id" in query:
                where_clauses.append("message_id = ?")
                params.append(query["message_id"])
            
            if where_clauses:
                sql += " WHERE " + " AND ".join(where_clauses)
            
            if sort:
                if isinstance(sort, dict):
                    sort_items = list(sort.items())
                else:
                    sort_items = sort
                for sort_field, direction in sort_items:
                    sql += f" ORDER BY {sort_field} {'DESC' if direction == -1 else 'ASC'}"
                    break  # 只支持一个排序字段
            
            if limit:
                sql += f" LIMIT {limit}"
            
            cursor.execute(sql, params)
            rows = []
            for row in cursor.fetchall():
                r = dict(row)
                r.setdefault("_id", r.get("id"))  # 兼容 MongoDB 风格的 _id 访问
                rows.append(r)
            return rows
        
        elif self.name in ["graph_data.nodes", "graph_nodes"]:
            sql = "SELECT * FROM graph_nodes"
            cursor.execute(sql)
            return [dict(row) for row in cursor.fetchall()]
        
        elif self.name in ["graph_data.edges", "graph_edges"]:
            sql = "SELECT * FROM graph_edges"
            cursor.execute(sql)
            return [dict(row) for row in cursor.fetchall()]
        
        elif self.name == "llm_usage":
            sql = "SELECT * FROM llm_usage"
            params = []
            if "timestamp" in query and "$gte" in query["timestamp"]:
                sql += " WHERE timestamp >= ?"
                params.append(query["timestamp"]["$gte"])
            cursor.execute(sql, params)
            return [dict(row) for row in cursor.fetchall()]
        
        elif self.name == "chat_streams":
            sql = "SELECT * FROM chat_streams"
            cursor.execute(sql)
            return [dict(row) for row in cursor.fetchall()]
        
        elif self.name == "relationships":
            sql = "SELECT * FROM relationships"
            cursor.execute(sql)
            return [dict(row) for row in cursor.fetchall()]
        
        elif self.name == "image_descriptions":
            sql = "SELECT * FROM image_descriptions WHERE hash = ? AND type = ?"
            cursor.execute(sql, (query.get("hash"), query.get("type")))
            return [dict(row) for row in cursor.fetchall()]
        
        elif self.name == "emoji":
            sql = "SELECT * FROM emoji"
            params = []
            where_clauses = []
            if "hash" in query:
                where_clauses.append("hash = ?")
                params.append(query["hash"])
            if where_clauses:
                sql += " WHERE " + " AND ".join(where_clauses)
            if limit:
                sql += f" LIMIT {limit}"
            cursor.execute(sql, params)
            rows = []
            import ast
            for row in cursor.fetchall():
                r = dict(row)
                try:
                    r["embedding"] = ast.literal_eval(r["embedding"]) if r.get("embedding") else []
                except Exception:
                    r["embedding"] = []
                rows.append(r)
            return rows
        
        elif self.name == "recalled_messages":
            sql = "SELECT * FROM recalled_messages WHERE stream_id = ?"
            cursor.execute(sql, (query.get("stream_id"),))
            return [dict(row) for row in cursor.fetchall()]
        
        elif self.name == "knowledges":
            sql = "SELECT * FROM knowledges"
            cursor.execute(sql)
            return [dict(row) for row in cursor.fetchall()]
        
        return []
    
    def update_one(self, query: Dict[str, Any], update: Dict[str, Any], upsert: bool = False) -> None:
        """更新单个文档"""
        cursor = self.db.cursor()
        if self.name == "messages":
            if "$set" in update:
                set_data = update["$set"]
                if "memorized" in set_data:
                    sql = "UPDATE messages SET memorized = ? WHERE id = ?"
                    cursor.execute(sql, (set_data["memorized"], query.get("_id") or query.get("id")))
                elif "memorized_times" in set_data:
                    sql = "UPDATE messages SET memorized_times = ? WHERE id = ?"
                    cursor.execute(sql, (set_data["memorized_times"], query.get("_id") or query.get("id")))
        elif self.name == "chat_streams":
            if "$set" in update:
                set_data = update["$set"]
                if upsert:
                    # INSERT OR REPLACE
                    sql = """INSERT OR REPLACE INTO chat_streams 
                             (stream_id, platform, user_info, group_info, last_message_time, created_time, message_count) 
                             VALUES (?, ?, ?, ?, ?, ?, ?)"""
                    cursor.execute(sql, (
                        query.get("stream_id"),
                        set_data.get("platform"),
                        json.dumps(set_data.get("user_info", {})),
                        json.dumps(set_data.get("group_info", {})),
                        set_data.get("last_message_time"),
                        set_data.get("created_time"),
                        set_data.get("message_count", 0)
                    ))
                else:
                    # UPDATE
                    sql = """UPDATE chat_streams SET 
                             platform = ?, user_info = ?, group_info = ?, last_message_time = ?, message_count = ? 
                             WHERE stream_id = ?"""
                    cursor.execute(sql, (
                        set_data.get("platform"),
                        json.dumps(set_data.get("user_info", {})),
                        json.dumps(set_data.get("group_info", {})),
                        set_data.get("last_message_time"),
                        set_data.get("message_count", 0),
                        query.get("stream_id")
                    ))
        elif self.name in ["graph_data.nodes", "graph_nodes"]:
            if "$set" in update:
                set_data = update["$set"]
                sql = "UPDATE graph_nodes SET memory_items = ?, last_modified = ? WHERE concept = ?"
                cursor.execute(sql, (str(set_data.get("memory_items", [])), set_data.get("last_modified"), query["concept"]))
        elif self.name in ["graph_data.edges", "graph_edges"]:
            if "$set" in update:
                set_data = update["$set"]
                sql = "UPDATE graph_edges SET hash = ? WHERE source = ? AND target = ?"
                cursor.execute(sql, (set_data.get("hash"), query["source"], query["target"]))
        elif self.name == "image_descriptions":
            if "$set" in update:
                set_data = update["$set"]
                sql = "UPDATE image_descriptions SET description = ? WHERE hash = ? AND type = ?"
                cursor.execute(sql, (set_data.get("description"), query["hash"], query["type"]))
        elif self.name == "images":
            if "$set" in update:
                set_data = update["$set"]
                if upsert:
                    sql = """INSERT OR REPLACE INTO images (hash, type, url, path)
                             VALUES (?, ?, ?, ?)"""
                    cursor.execute(sql, (
                        query.get("hash") or set_data.get("hash"),
                        set_data.get("type"),
                        set_data.get("url"),
                        set_data.get("path"),
                    ))
                else:
                    sql = "UPDATE images SET path = ? WHERE hash = ? AND type = ?"
                    cursor.execute(sql, (set_data.get("path"), query.get("hash"), set_data.get("type")))
        elif self.name == "relationships":
            if "$set" in update:
                set_data = update["$set"]
                sql = "UPDATE relationships SET nickname = ?, relationship_value = ?, gender = ?, age = ?, saved = ? WHERE user_id = ? AND platform = ?"
                cursor.execute(sql, (
                    set_data.get("nickname"), 
                    set_data.get("relationship_value"), 
                    set_data.get("gender"), 
                    set_data.get("age"), 
                    1 if set_data.get("saved", False) else 0,
                    query["user_id"], 
                    query["platform"]
                ))
        elif self.name == "emoji":
            if "$inc" in update:
                inc_data = update["$inc"]
                if "usage_count" in inc_data:
                    sql = "UPDATE emoji SET usage_count = usage_count + ? WHERE id = ?"
                    cursor.execute(sql, (inc_data["usage_count"], query.get("_id") or query.get("id")))
            elif "$set" in update:
                set_data = update["$set"]
                set_clauses = []
                params = []
                for col in ("path", "hash", "embedding", "discription", "timestamp", "usage_count"):
                    if col in set_data:
                        set_clauses.append(f"{col} = ?")
                        params.append(str(set_data[col]) if col == "embedding" else set_data[col])
                if set_clauses:
                    id_ = query.get("_id") or query.get("id")
                    params.append(id_)
                    sql = f"UPDATE emoji SET {', '.join(set_clauses)} WHERE id = ?"
                    cursor.execute(sql, params)
        self.db.commit()
        """删除单个文档"""
        cursor = self.db.cursor()
        if self.name in ["graph_data.nodes", "graph_nodes"]:
            sql = "DELETE FROM graph_nodes WHERE concept = ?"
            cursor.execute(sql, (query["concept"],))
        elif self.name in ["graph_data.edges", "graph_edges"]:
            sql = "DELETE FROM graph_edges WHERE source = ? AND target = ?"
            cursor.execute(sql, (query["source"], query["target"]))
        self.db.commit()
    
    def delete_many(self, query: Dict[str, Any]) -> None:
        """删除多个文档"""
        cursor = self.db.cursor()
        if self.name == "recalled_messages":
            if "$lt" in query.get("time", {}):
                sql = "DELETE FROM recalled_messages WHERE time < ?"
                cursor.execute(sql, (query["time"]["$lt"],))
        elif self.name in ["graph_data.edges", "graph_edges"]:
            if "$or" in query:
                conditions = []
                params = []
                for condition in query["$or"]:
                    if "source" in condition:
                        conditions.append("source = ?")
                        params.append(condition["source"])
                    elif "target" in condition:
                        conditions.append("target = ?")
                        params.append(condition["target"])
                if conditions:
                    sql = f"DELETE FROM graph_edges WHERE {' OR '.join(conditions)}"
                    cursor.execute(sql, params)
        elif self.name == "graph_data":
            sql = "DELETE FROM graph_nodes; DELETE FROM graph_edges;"
            cursor.executescript(sql)
        self.db.commit()
    
    def count_documents(self, query: Dict[str, Any] = None) -> int:
        """计数文档"""
        cursor = self.db.cursor()
        if query is None:
            query = {}
        
        if self.name in ["graph_data.nodes", "graph_nodes"]:
            sql = "SELECT COUNT(*) FROM graph_nodes WHERE concept = ?"
            cursor.execute(sql, (query.get("concept"),))
        elif self.name in ["graph_data.edges", "graph_edges"]:
            sql = "SELECT COUNT(*) FROM graph_edges WHERE source = ? OR target = ?"
            cursor.execute(sql, (query.get("concept"), query.get("concept")))
        elif self.name == "emoji":
            sql = "SELECT COUNT(*) FROM emoji"
            cursor.execute(sql)
        else:
            return 0
        
        return cursor.fetchone()[0]

    def delete_one(self, query: Dict[str, Any]) -> object:
        """删除单个文档，返回具有 deleted_count 属性的结果对象

        支持常见集合和查询键：_id/id/hash/filename/date/stream_id/concept/source/target 等。
        """
        from types import SimpleNamespace

        cursor = self.db.cursor()
        if not query:
            return SimpleNamespace(deleted_count=0)

        try:
            # schedule
            if self.name == "schedule":
                cursor.execute("DELETE FROM schedule WHERE date = ?", (query.get("date"),))

            # messages
            elif self.name == "messages":
                id_ = query.get("_id") or query.get("id")
                if id_:
                    cursor.execute("DELETE FROM messages WHERE id = ?", (id_,))
                elif "message_id" in query:
                    cursor.execute("DELETE FROM messages WHERE message_id = ?", (query.get("message_id"),))

            # graph nodes / edges
            elif self.name in ["graph_data.nodes", "graph_nodes"]:
                cursor.execute("DELETE FROM graph_nodes WHERE concept = ?", (query.get("concept"),))
            elif self.name in ["graph_data.edges", "graph_edges"]:
                cursor.execute(
                    "DELETE FROM graph_edges WHERE source = ? AND target = ?",
                    (query.get("source"), query.get("target")),
                )

            # emoji
            elif self.name == "emoji":
                id_ = query.get("_id") or query.get("id")
                if id_:
                    cursor.execute("DELETE FROM emoji WHERE id = ?", (id_,))
                elif "hash" in query:
                    cursor.execute("DELETE FROM emoji WHERE hash = ?", (query.get("hash"),))
                elif "filename" in query:
                    cursor.execute("DELETE FROM emoji WHERE filename = ?", (query.get("filename"),))

            # images / image_descriptions
            elif self.name == "images":
                if "hash" in query and "type" in query:
                    cursor.execute("DELETE FROM images WHERE hash = ? AND type = ?", (query.get("hash"), query.get("type")))
                elif "hash" in query:
                    cursor.execute("DELETE FROM images WHERE hash = ?", (query.get("hash"),))
            elif self.name == "image_descriptions":
                cursor.execute("DELETE FROM image_descriptions WHERE hash = ? AND type = ?", (query.get("hash"), query.get("type")))

            # recalled_messages
            elif self.name == "recalled_messages":
                if "$lt" in query.get("time", {}):
                    cursor.execute("DELETE FROM recalled_messages WHERE time < ?", (query["time"]["$lt"],))
                elif "stream_id" in query:
                    cursor.execute("DELETE FROM recalled_messages WHERE stream_id = ?", (query.get("stream_id"),))

            # relationships
            elif self.name == "relationships":
                if "user_id" in query and "platform" in query:
                    cursor.execute("DELETE FROM relationships WHERE user_id = ? AND platform = ?", (query.get("user_id"), query.get("platform")))

            # fallback: try delete by id on table named after collection
            else:
                id_ = query.get("_id") or query.get("id")
                if id_:
                    try:
                        cursor.execute(f"DELETE FROM {self.name} WHERE id = ?", (id_,))
                    except Exception:
                        # 无法执行通用删除，返回0
                        pass

            self.db.commit()
            return SimpleNamespace(deleted_count=cursor.rowcount)

        except Exception:
            # 任何异常都不要让调用方崩溃，记录并返回0
            try:
                self.db.rollback()
            except Exception:
                pass
            return SimpleNamespace(deleted_count=0)
    
    def aggregate(self, pipeline: List[Dict[str, Any]]):
        """聚合查询（简化实现）"""
        # 这里只实现简单的聚合，对于复杂的需要更复杂的SQL
        if self.name == "knowledges":
            cursor = self.db.cursor()
            # 简化实现：返回所有knowledges，按id排序
            sql = "SELECT * FROM knowledges ORDER BY id DESC LIMIT 10"
            cursor.execute(sql)
            return [dict(row) for row in cursor.fetchall()]
        return []
    
    def create_index(self, keys: List, **kwargs):
        """创建索引（SQLite中索引已通过表定义创建）"""
        pass
    
    def drop_indexes(self):
        """删除索引（SQLite中索引已通过表定义创建）"""
        pass
    
    def list_collection_names(self):
        """列出集合名（模拟）"""
        return ["messages", "recalled_messages", "graph_data", "llm_usage", "chat_streams", "schedule", 
                "images", "image_descriptions", "relationships", "knowledges", 
                "reasoning_logs", "emoji", "store_memory_dots"]
    
    def create_collection(self, name: str):
        """创建集合（SQLite中表已预创建）"""
        pass


# 全局数据库访问点
db: DBWrapper = DBWrapper()
