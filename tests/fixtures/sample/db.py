def connect():
    return {}


class Store:
    def __init__(self, conn):
        self.conn = conn

    def get(self, key):
        return self.conn.get(key)
