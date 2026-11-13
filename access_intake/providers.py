"""Provider boundary; implementation follows its first failing contract test."""

class MockProvider:
    def __init__(self, mock_dir, scripts=None):
        pass

    def extract(self, message, document_id):
        return None
