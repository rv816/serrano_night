import logging
import functools
from django.conf.urls import patterns, url
from django.core.urlresolvers import reverse
from django.http import HttpResponse
from preserialize.serialize import serialize
from restlib2.http import codes
from restlib2.params import Parametizer, param_cleaners
from avocado.events import usage
from avocado.models import DataConcept, DataCategory
from avocado.conf import OPTIONAL_DEPS
from serrano.resources.field import FieldResource
from .base import DataResource, SAFE_METHODS
from . import templates
from .field import base as FieldResources

can_change_concept = lambda u: u.has_perm('avocado.change_dataconcept')
log = logging.getLogger(__name__)

def has_orphaned_field(instance):
    for cfield in instance.concept_fields.select_related('field').iterator():
        if FieldResources.is_field_orphaned(cfield.field):
            return True
    return False

def concept_posthook(instance, data, request, embed, brief, categories=None):
    """Concept serialization post-hook for augmenting per-instance data.

    The only two arguments the post-hook takes is instance and data. The
    remaining arguments must be partially applied using `functools.partial`
    during the request/response cycle.
    """
    uri = request.build_absolute_uri

    if categories is None:
        categories = {}

    if 'category_id' in data:
        # This relies on categories being passed in as a dict with the key being
        # the primary key. This makes it must faster since the categories are
        # pre-cached
        category = categories.get(data.pop('category_id'))
        data['category'] = serialize(category, **templates.Category)

        if data['category']:
            parent = categories.get(data['category'].pop('parent_id'))
            data['category']['parent'] = serialize(parent, **templates.Category)

            # Embed first parent as well, but no others since this is the bound
            # in Avocado's DataCategory parent field.
            if data['category']['parent']:
                data['category']['parent'].pop('parent_id')

    if not brief:
        data['_links'] = {
            'self': {
                'href': uri(reverse('serrano:concept', args=[instance.pk])),
            },
            'fields': {
                'href': uri(reverse('serrano:concept-fields', args=[instance.pk])),
            }
        }

    # Embeds the related fields directly in the concept output
    if not brief and embed:
        resource = ConceptFieldsResource()
        data['fields'] = resource.prepare(request, instance)

    return data


class ConceptParametizer(Parametizer):
    "Supported params and their defaults for Concept endpoints."

    sort = None
    order = 'asc'
    published = None
    archived = None
    embed = False
    brief = False
    query = ''
    limit = None

    def clean_embed(self, value):
        return param_cleaners.clean_bool(value)

    def clean_brief(self, value):
        return param_cleaners.clean_bool(value)

    def clean_published(self, value):
        return param_cleaners.clean_bool(value)

    def clean_archived(self, value):
        return param_cleaners.clean_bool(value)

    def clean_query(self, value):
        return param_cleaners.clean_string(value)

    def clean_limit(self, value):
        return param_cleaners.clean_int(value)


class ConceptBase(DataResource):
    "Base resource for Concept-related data."

    model = DataConcept

    template = templates.Concept

    parametizer = ConceptParametizer

    def get_queryset(self, request):
        queryset = self.model.objects.all()
        if not can_change_concept(request.user):
            queryset = queryset.published()
        return queryset

    def get_object(self, request, **kwargs):
        queryset = self.get_queryset(request)
        try:
            return queryset.get(**kwargs)
        except self.model.DoesNotExist:
            pass

    def _get_categories(self, request, objects):
        """Returns a QuerySet of categories for use during serialization.

        Since `category` is a nullable relationship to `concept`, a lookup
        would have to occur for every concept being serialized. This returns
        a QuerySet applicable to the resource using it and is cached for the
        remainder of the request/response cycle.
        """
        return dict((x.pk, x) for x in list(DataCategory.objects.all()))

    def prepare(self, request, objects, template=None, embed=False,
            brief=False, **params):

        if template is None:
            template = templates.BriefConcept if brief else self.template

        if brief:
            categories = {}
        else:
            categories = self._get_categories(request, objects)

        posthook = functools.partial(concept_posthook, request=request,
            embed=embed, brief=brief, categories=categories)

        return serialize(objects, posthook=posthook, **template)

    def is_forbidden(self, request, response, *args, **kwargs):
        "Ensure non-privileged users cannot make any changes."
        if request.method not in SAFE_METHODS and not can_change_concept(request.user):
            return True

    def is_not_found(self, request, response, pk, *args, **kwargs):
        instance = self.get_object(request, pk=pk)
        if instance is None:
            return True
        request.instance = instance
        return False


class ConceptResource(ConceptBase):
    "Resource for interacting with Concept instances."
    def get(self, request, pk):
        params = self.get_params(request)
        instance = request.instance
        
        if params.get('embed', False):
            for cf in instance.concept_fields.select_related('field').iterator():
                log.error("Concept with ID={0} has orphaned field "
                    "{1}.{2}.{3}. with id {4}".format(instance.pk, 
                        cf.field.app_name, cf.field.model_name, 
                        cf.field.field_name, cf.field.pk))
            return HttpResponse(status=codes.internal_server_error,
                content="Could not get concept because it has one or more "
                    "orphaned fields.")

        usage.log('read', instance=instance, request=request)
        return self.prepare(request, instance, embed=params['embed'])


class ConceptFieldsResource(ConceptBase):
    "Resource for interacting with fields specific to a Concept instance."
    def prepare(self, request, instance, template=None, **params):
        if template is None:
            template = templates.ConceptField

        fields = []
        resource = FieldResource()

        has_orphaned_field = False
        for cfield in instance.concept_fields.select_related('field').iterator():
            if FieldResources.is_field_orphaned(cfield.field):
                log.error("Concept with ID={0} has orphaned concept field for "
                    "field {1}.{2}.{3} with id {4}".format(instance.pk, 
                        cfield.field.app_name, cfield.field.model_name, 
                        cfield.field.field_name, cfield.field.pk))
                has_orphaned_field = True
                continue

            field = resource.prepare(request, cfield.field)
            # Add the alternate name specific to the relationship between the
            # concept and the field.
            field.update(serialize(cfield, **template))
            fields.append(field)

        if has_orphaned_field:
            return HttpResponse(status=codes.internal_server_error,
                content="Could not get concept fields because one or more are "
                    "linked to orphaned fields.")

        return fields

    def get(self, request, pk):
        instance = request.instance
        usage.log('fields', instance=instance, request=request)
        return self.prepare(request, instance)


class ConceptsResource(ConceptBase):
    def is_not_found(self, request, response, *args, **kwargs):
        return False

    def get(self, request, pk=None):
        params = self.get_params(request)

        queryset = self.get_queryset(request)

        # For privileged users, check if any filters are applied, otherwise
        # only allow for published objects.
        if can_change_concept(request.user):
            filters = {}

            if params['published'] is not None:
                filters['published'] = params['published']

            if params['archived'] is not None:
                filters['archived'] = params['archived']

            if filters:
                queryset = queryset.filter(**filters)
        else:
            queryset = queryset.published()

        # If Haystack is installed, perform the search
        if params['query'] and OPTIONAL_DEPS['haystack']:
            usage.log('search', model=self.model, request=request, data={
                'query': params['query'],
            })
            results = self.model.objects.search(params['query'],
                queryset=queryset, max_results=params['limit'],
                partial=True)
            objects = (x.object for x in results)
        else:
            if params['sort'] == 'name':
                order = '-name' if params['order'] == 'desc' else 'name'
                queryset = queryset.order_by(order)

            if params['limit']:
                queryset = queryset[:params['limit']]

            objects = queryset
        
        if params.get('embed', None):
            orphans = [o for o in objects if has_orphaned_field(o)]
            orphan_pks = []
            for o in orphans:
                log.warning("Truncating concept(id={0}) with orphaned "
                    "field.".format(o.pk))
                orphan_pks.append(o.pk)
            objects = objects.exclude(pk__in=orphan_pks)

        return self.prepare(request, objects, **params)


concept_resource = ConceptResource()
concept_fields_resource = ConceptFieldsResource()
concepts_resource = ConceptsResource()

# Resource endpoints
urlpatterns = patterns('',
    url(r'^$', concepts_resource, name='concepts'),
    url(r'^(?P<pk>\d+)/$', concept_resource, name='concept'),
    url(r'^(?P<pk>\d+)/fields/$', concept_fields_resource, name='concept-fields'),
)
