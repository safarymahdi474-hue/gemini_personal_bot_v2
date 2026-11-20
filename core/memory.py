class MemoryManager:
    def __init__(self, db, max_chars=1800, limit=30):
        self.db = db
        self.max_chars = max_chars
        self.limit = limit

    def context(self, user_id, max_chars=None):
        max_chars = max_chars or self.max_chars
        rows = self.db.memories(user_id, self.limit)
        if not rows:
            return ""
        # Newest first; keep whole lines only so the prompt never ends mid-sentence.
        lines, used = [], 0
        for row in rows:
            line = f"- {row['content']}"
            if used + len(line) + 1 > max_chars:
                if not lines:  # a single huge memory: keep its beginning
                    lines.append(line[:max_chars])
                break
            lines.append(line)
            used += len(line) + 1
        return "\n".join(lines)

    def add(self, user_id, content):
        return self.db.add_memory(user_id, content.strip())

    def list(self, user_id):
        return self.db.memories(user_id, self.limit)

    def forget(self, user_id, memory_id):
        return self.db.forget(user_id, memory_id)
