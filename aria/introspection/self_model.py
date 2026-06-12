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

    @property
    def components(self):
        return self.introspection_data.get("components", {})

    def add_failure_pattern(self, component_name: str, pattern: str):
        if "components" not in self.introspection_data:
            self.introspection_data["components"] = {}
        if component_name not in self.introspection_data["components"]:
            self.introspection_data["components"][component_name] = {"recent_failure_patterns": [], "known_weaknesses": []}
            
        if pattern not in self.introspection_data["components"][component_name]["recent_failure_patterns"]:
            self.introspection_data["components"][component_name]["recent_failure_patterns"].append(pattern)

    def add_weakness(self, component_name: str, weakness: str):
        if "components" not in self.introspection_data:
            self.introspection_data["components"] = {}
        if component_name not in self.introspection_data["components"]:
            self.introspection_data["components"][component_name] = {"recent_failure_patterns": [], "known_weaknesses": []}
            
        if weakness not in self.introspection_data["components"][component_name]["known_weaknesses"]:
            self.introspection_data["components"][component_name]["known_weaknesses"].append(weakness)

    def add_system_pattern(self, pattern: str):
        if "system_wide_patterns" not in self.introspection_data:
            self.introspection_data["system_wide_patterns"] = []
            
        if pattern not in self.introspection_data["system_wide_patterns"]:
            self.introspection_data["system_wide_patterns"].append(pattern)

self_model = SelfModel()