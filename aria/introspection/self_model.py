import json

class SelfModel:
    def __init__(self):
        self.introspection_data = {}

    def add_introspection_data(self, key, value):
        self.introspection_data[key] = value

    def get_introspection_data(self):
        return self.introspection_data

    def get_model(self):
        return self.introspection_data

    def record_cycle(self, component_name: str, success: bool, **kwargs):
        if "cycles" not in self.introspection_data:
            self.introspection_data["cycles"] = {}
        if component_name not in self.introspection_data["cycles"]:
            self.introspection_data["cycles"][component_name] = {"success": 0, "failure": 0}
            
        if success:
            self.introspection_data["cycles"][component_name]["success"] += 1
        else:
            self.introspection_data["cycles"][component_name]["failure"] += 1

self_model = SelfModel()