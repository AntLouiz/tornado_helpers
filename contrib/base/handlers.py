import json
from urllib.parse import urlparse, parse_qs, urlencode
from cached_property import cached_property
from tornado.web import RequestHandler
from tornado.web import Finish
from contrib.auth.authenticators import BaseAuthentication
from contrib.base.models import MongoModel
from contrib.base.permissions import BasePermission
from contrib.base.pagination import Paginator, EmptyPage


class MongoAPIMixin(RequestHandler):
    model = MongoModel
    lookup_field = '_id'
    lookup_url_kwarg = 'id'
    page_size = 20
    pagination_class = Paginator
    authentication_class = BaseAuthentication
    permissions_classes = [BasePermission]

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        collection = self.db[self.model.Meta.collection_name]
        self.model.manager = self.model._manager(collection)

    @cached_property
    def db_client(self):
        return self.settings['db_client']

    @cached_property
    def db(self):
        return self.db_client[self.settings['db_name']]

    async def check_permissions(self):
        for permission_class in self.permissions_classes:
            permission_obj = permission_class()
            has_permission = await permission_obj.has_permission(self.request, self)
            if not has_permission:
                return self.json_response(permission_class.message, 403)

    async def check_object_permissions(self, obj):
        for permission_class in self.permissions_classes:
            permission_obj = permission_class()
            has_permission = await permission_obj.has_object_permission(self.request, obj)
            if not has_permission:
                return self.json_response(permission_class.message, 403)

    async def check_authentication(self):
        auhentication = self.authentication_class()
        is_authenticated = await auhentication.authenticate(self.request, self)
        if not is_authenticated:
            return self.json_response(auhentication.unauthorized_message, 401)

    def json_response(self, data={}, status=200):
        self.write(json.dumps(data))
        self.set_status(status)
        raise Finish()

    def get_body_data(self):
        body = self.request.body
        body_data = json.loads(body)

        return body_data

    def get_data(self):
        body_data = self.get_body_data()
        data = self.model(body_data)
        data.validate()
        return data

    async def prepare(self, *args, **kwargs):
        self.query_filter = self.extract_query_args()
        self.page = self.query_filter.pop('page', 0)
        self.page_size = self.query_filter.pop('page_size', self.page_size)
        self.model.manager.skip = self.page
        self.model.manager.limit = self.page_size
        await self.check_permissions()
        await self.check_authentication()

    def extract_query_args(self):
        query_args = {}
        request_query_args = self.request.query_arguments

        if not request_query_args:
            return query_args

        for key in request_query_args:
            list_data = request_query_args[key]
            encoded_arg = list_data.pop()
            query_arg = encoded_arg.decode('utf8')
            splited_query_arg = query_arg.split(',')

            query_arg = splited_query_arg
            key = key.replace('__', '.')

            try:
                query_arg = [int(arg) for arg in splited_query_arg]
                if len(query_arg) == 1:
                    query_arg = query_arg[0]
                query_args[key] = query_arg

            except (TypeError, ValueError):
                if len(splited_query_arg) == 1:
                    query_arg = splited_query_arg[0]
                query_args[key] = query_arg

        return query_args

    def process_response(self, queryset, *args, **kwargs):
        return queryset.asdict()

    def paginate_response(self, queryset, current_page=1, page_size=None):
        data = queryset._value
        count = queryset.total

        page_size = self.page_size if not page_size else page_size

        next_page = None
        prev_page = None
        full_uri = self.request.uri
        parsed_full_uri = urlparse(full_uri)
        parsed_query_params = parse_qs(parsed_full_uri.query)

        # split query args
        uri = full_uri.split('?')[0]

        try:
            if type(data) is not list:
                data = [data]

            current_page = int(current_page)
            paginator = self.pagination_class(data, page_size, count)
            paginated_data = paginator.page(current_page)

            if paginated_data.has_next():
                next_page_number = paginated_data.next_page_number()
                parsed_query_params['page'] = [next_page_number]
                encoded_query_params = urlencode(query=parsed_query_params, doseq=True)

                next_page = f'{uri}?{encoded_query_params}'
            if int(current_page) > 1:
                prev_page_number = paginated_data.previous_page_number()
                parsed_query_params['page'] = [prev_page_number]
                encoded_query_params = urlencode(query=parsed_query_params, doseq=True)

                prev_page = f'{uri}?{encoded_query_params}'

            results = paginated_data.object_list

        except (EmptyPage, ValueError):
            results = []

        paginated_response = {
            'count': count,
            'next': next_page,
            'previous': prev_page,
            'results': results
        }

        return paginated_response

    def validate_body_data(self):
        data = self.get_body_data()
        model_attrs = self.model.fields
        model_protected_attrs = self.model.get_protected_fields()

        data_fields = data.keys()
        model_fields = model_attrs.keys()

        has_right_fields = all(field in model_fields for field in data_fields)
        has_protected_fields = any(field in model_protected_attrs for field in data_fields)

        if not has_right_fields:
            self.json_response({'error': ["Campo(s) não existente(s) no modelo."]}, 400)
            raise Finish()

        if has_protected_fields:
            self.json_response({'error': ["Não são permitidas alterações de campos protegidos."]}, 400)
            raise Finish()

    async def get_queryset(self, many=True):
        fields_to_remove = self.model.Options.roles['public'].fields
        queryset = await self.model.manager.find(self.query_filter,
                                                 many=many,
                                                 remove_fields=fields_to_remove)
        return queryset


class ModelAPIView(MongoAPIMixin):

    async def get(self, *args, **kwargs):
        view = self.list
        self.lookup_arg = kwargs.get(self.lookup_url_kwarg)
        if self.lookup_arg:
            view = self.retrieve

        return await view(*args, **kwargs)

    async def list(self, *args, **kwargs):
        queryset = await self.get_queryset()
        response = self.process_response(queryset)
        response = self.paginate_response(queryset)
        return self.json_response(data=response)

    async def retrieve(self, object_id):
        self.query_filter[self.lookup_field] = self.lookup_arg

        queryset = await self.get_queryset(many=False)
        response = self.process_response(queryset)
        if not response:
            self.json_response({'error': ["Registro não encontrado."]}, 404)
            return

        return self.json_response(data=response)

    async def post(self, *args, **kwargs):
        post_data = self.get_data()
        model_object = self.model(post_data)
        model_object.is_valid()

        new_object = await self.model.manager.create(model_object)
        response = new_object

        return self.json_response(data=response, status=201)

    async def patch(self, object_id):
        self.validate_body_data()
        data = self.get_data()

        result = await self.model.manager.update({"_id": object_id}, data)
        if not result:
            self.json_response({'error': ["Registro não alterado."]}, 400)
            return

        return self.json_response(data=data, status=200)

    async def delete(self, object_id):
        query_filter = {"_id": object_id}
        result = await self.model.manager.delete(query_filter)
        if not result:
            self.json_response({'error': ["Não foi possível remover o registro."]}, 400)
            return

        response = {"_id": object_id}
        return self.json_response(data=response, status=200)


class CreateAPIView(MongoAPIMixin):
    async def post(self, *args, **kwargs):
        post_data = self.get_data()
        model_object = self.model(post_data)
        model_object.is_valid()

        new_object = await self.model.manager.create(model_object)
        response = new_object

        return self.json_response(data=response, status=201)


class RetrieveAPIView(MongoAPIMixin):
    async def get(self, *args, **kwargs):
        view = self.list
        self.lookup_arg = kwargs.get(self.lookup_url_kwarg)
        if self.lookup_arg:
            view = self.retrieve

        return await view(*args, **kwargs)
