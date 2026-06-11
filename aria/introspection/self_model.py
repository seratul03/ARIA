import json

class SelfModel:
    def __init__(self):
        self.introspection_data = {}

    def add_introspection_data(self, key, value):
        self.introspection_data[key] = value

    def get_introspection_data(self):
        return self.introspection_data

self_model = SelfModel()