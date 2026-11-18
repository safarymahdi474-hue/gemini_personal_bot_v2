class MemoryManager:
    def __init__(self, db):
        self.db = db

    def context(self, user_id, max_chars=1800):
        rows = self.db.memories(user_id)
        if not rows:
            return ""
        lines = [f"- {row['content']}" for row in rows]
        # Bound persistent memory context to control prompt tokens.
        result = "\n".join(lines)
        return result[:max_chars]

    def add(self, user_id, content):
        return self.db.add_memory(user_id, content.strip())

    def list(self, user_id):
        return self.db.memories(user_id)

    def forget(self, user_id, memory_id):
        return self.db.forget(user_id, memory_id)
