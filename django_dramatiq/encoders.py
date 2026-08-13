import json
from decimal import Decimal
from uuid import UUID

from dramatiq.encoder import JSONEncoder as DramatiqJSONEncoder
from dramatiq.encoder import MessageData


class ExtendJSONEncoder(json.JSONEncoder):
    def default(self, obj):
        if isinstance(obj, UUID):
            return str(obj)
        if isinstance(obj, Decimal):
            return str(obj)
        return json.JSONEncoder.default(self, obj)


class JSONEncoder(DramatiqJSONEncoder):
    """Encodes messages as JSON, with support for UUID and Decimal values.

    Subclasses dramatiq's JSONEncoder so that code which checks for a
    JSON-capable encoder (e.g. TaskAdmin.message_details) treats this as
    one; only encoding differs, decoding is inherited.
    """

    def encode(self, data: MessageData) -> bytes:
        return json.dumps(data, cls=ExtendJSONEncoder, separators=(",", ":")).encode("utf-8")
