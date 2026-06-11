import json

class SelfModel:
    def __init__(self):
        self.components = {}
        self.connections = {}

    def add_component(self, name, description):
        self.components[name] = description

    def add_connection(self, component1, component2):
        self.connections[(component1, component2)] = True

    def to_json(self):
        return json.dumps(self.__dict__)

# Example usage:
# self_model = SelfModel()
# self_model.add_component('Component 1', 'This is component 1')
# self_model.add_connection('Component 1', 'Component 2')
# print(self_model.to_json())