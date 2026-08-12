import json

from azure.servicebus import ServiceBusClient, ServiceBusMessage

from api.core.config import settings


class Messenger:
    def __init__(self):
        self.connection_string = settings.SERVICE_BUS_CONNECTION_STRING
        self.topic_name = settings.SERVICE_BUS_TOPIC_NAME

    def send_message(self, message: dict):
        with ServiceBusClient.from_connection_string(self.connection_string) as client:
            sender = client.get_topic_sender(topic_name=self.topic_name)
            with sender:
                service_bus_message = ServiceBusMessage(json.dumps(message))
                sender.send_messages(service_bus_message)
