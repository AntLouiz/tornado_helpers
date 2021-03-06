import json
from bson import json_util
from bson import ObjectId
from pymongo import DESCENDING
from collections import namedtuple


class Queryset:
    def __init__(self, value, total=0):
        self._value = value
        self.total = total

    def asdict(self):
        return self._value

    def __repr__(self):
        value_count = len(self._value) if self.total > 1 else self.total
        return f'<Queryset ({value_count} of {self.total})>'

class MotorModelManager:
    skip = None
    limit = None

    def __init__(self, collection):
        self.collection = collection

    async def find(self, query_filter={}, many=True,
                   sort=('_id', DESCENDING), remove_fields=[], limit=None):

        query_filter = self._parse_query_filter(query_filter)
        fields_to_remove = self._parse_remove_fields(remove_fields)
        count = await self.collection.count_documents(query_filter)

        if many:
            cursor = self.collection.find(query_filter, fields_to_remove)
            if self.skip:
                cursor.skip(self.skip)
            if limit:
                cursor.limit(limit)
            if self.limit:
                cursor.limit(self.limit)
            if sort:
                cursor.sort([sort])

            results = await cursor.to_list(None)
        else:
            results = await self.collection.find_one(query_filter, fields_to_remove)

        parsed_results = self._parse_json(results)

        queryset = Queryset(parsed_results, count)
        return queryset

    async def create(self, model_object):
        data = model_object.to_primitive()
        data.pop('_id', None)

        result = await self.collection.insert_one(data)
        data['_id'] = str(result.inserted_id)
        return data

    async def update(self, query_filter, data):
        query_filter = self._convert_id_field(query_filter)
        data = {
            "$set": data
        }
        future = self.collection.update_one(query_filter, data)

        if len(data) > 1:
            future = self.collection.update_many(query_filter, data)

        result = await future

        return result

    async def delete(self, query_filter):
        query_filter = self._convert_id_field(query_filter)
        result = await self.collection.delete_one(query_filter)
        return result

    async def create_index(self, fields, unique=True):
        await self.collection.create_index(fields, unique=unique)

    def get_collection(self):
        return self.collection

    def _parse_json(self, data):
        return json.loads(json_util.dumps(data))

    def _convert_id_field(self, query_filter):
        has_id_field = query_filter.get('_id')
        if has_id_field:
            query_filter['_id'] = ObjectId(has_id_field)
        return query_filter

    def _parse_query_filter(self, query_filter):
        query_filter = self._convert_id_field(query_filter)
        return query_filter

    def _parse_remove_fields(self, fields):
        if not fields:
            return None
        return {field: 0 for field in fields}
