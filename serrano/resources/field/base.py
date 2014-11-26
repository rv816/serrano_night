import functools
import logging
from django.core.urlresolvers import reverse
from preserialize.serialize import serialize
from restlib2.http import codes
from restlib2.params import Parametizer, StrParam, BoolParam, IntParam
from avocado.conf import OPTIONAL_DEPS
from avocado.models import DataField
from avocado.events import usage
from serrano.conf import settings
from ..base import ThrottledResource
from .. import templates

can_change_field = lambda u: u.has_perm('avocado.change_datafield')
log = logging.getLogger(__name__)


def is_field_orphaned(instance):
    if instance.model is None or instance.field is None:
        log.error("Field is an orphan.", extra={'field': instance.pk})
        return True
    return False


def field_posthook(instance, data, request):
    """Field serialization post-hook for augmenting per-instance data.

    The only two arguments the post-hook takes is instance and data. The
    remaining arguments must be partially applied using `functools.partial`
    during the request/response cycle.
    """

    uri = request.build_absolute_uri

    # Augment the links
    data['_links'] = {
        'self': {
            'href': uri(reverse('serrano:field',
                        args=[instance.pk])),
        }
    }

    # Add flag denoting the field is orphaned, otherwise add links to
    # supplementary resources.
    if is_field_orphaned(instance):
        data['orphaned'] = True
    else:
        data['_links']['values'] = {
            'href': uri(reverse('serrano:field-values',
                        args=[instance.pk])),
        }
        data['_links']['distribution'] = {
            'href': uri(reverse('serrano:field-distribution',
                        args=[instance.pk])),
        }

        stats_capable = settings.STATS_CAPABLE
        if stats_capable and stats_capable(instance):
            data['_links']['stats'] = {
                'href': uri(reverse('serrano:field-stats',
                            args=[instance.pk])),
            }

    return data


class FieldParametizer(Parametizer):
    "Supported params and their defaults for Field endpoints."

    sort = StrParam()
    order = StrParam('asc')
    unpublished = BoolParam(False)
    brief = BoolParam(False)
    query = StrParam()
    limit = IntParam()

    # Not implemented
    offset = IntParam()
    page = IntParam()


class FieldBase(ThrottledResource):
    model = DataField

    parametizer = FieldParametizer

    template = templates.Field

    def get_queryset(self, request):
        queryset = self.model.objects.all()
        if not can_change_field(request.user):
            queryset = queryset.published(user=request.user)
        return queryset

    def get_object(self, request, **kwargs):
        if not hasattr(request, 'instance'):
            queryset = self.get_queryset(request)

            try:
                instance = queryset.get(**kwargs)
            except self.model.DoesNotExist:
                instance = None

            request.instance = instance

        return request.instance

    def prepare(self, request, instance, template=None, brief=False, **params):
        if template is None:
            template = templates.BriefField if brief else self.template

        posthook = functools.partial(field_posthook, request=request)
        return serialize(instance, posthook=posthook, **template)

    def is_not_found(self, request, response, pk, *args, **kwargs):
        return self.get_object(request, pk=pk) is None


class FieldResource(FieldBase):
    "Resource for interacting with Field instances."

    def get(self, request, pk):
        instance = self.get_object(request, pk=pk)

        usage.log('read', instance=instance, request=request)

        # If the field is an orphan then log an error before returning an error
        if self.checks_for_orphans and is_field_orphaned(instance):
            data = {
                'message': 'Orphaned field',
            }
            return self.render(request, data,
                               status=codes.internal_server_error)

        return self.prepare(request, instance)


class FieldsResource(FieldResource):
    "Field Collection Resource"

    def is_not_found(self, request, response, *args, **kwargs):
        return False

    def get(self, request):
        params = self.get_params(request)
        queryset = self.get_queryset(request)

        # For privileged users, check if any filters are applied, otherwise
        # only allow for published objects.
        if not can_change_field(request.user) or not params['unpublished']:
            queryset = queryset.published()

        # If Haystack is installed, perform the search
        if params['query'] and OPTIONAL_DEPS['haystack']:
            usage.log('search', model=self.model, request=request, data={
                'query': params['query'],
            })
            results = self.model.objects.search(
                params['query'], queryset=queryset,
                max_results=params['limit'], partial=True)
            objects = (x.object for x in results)
        else:
            if params['sort'] == 'name':
                order = '-name' if params['order'] == 'desc' else 'name'
                queryset = queryset.order_by(order)

            if params['limit']:
                queryset = queryset[:params['limit']]

            objects = queryset

        if self.checks_for_orphans:
            pks = []
            for obj in objects:
                if not is_field_orphaned(obj):
                    pks.append(obj.pk)
            objects = self.model.objects.filter(pk__in=pks)

        return self.prepare(request, objects, **params)
