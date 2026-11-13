"""Durable intake storage boundary."""
class Store:
    def __init__(self,path):
        pass
    def save(self,document,result,settings=None,hook=None):
        return None
    def queue(self):
        return []
    def history(self,document_id):
        return []
