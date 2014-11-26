from decimal import Decimal
from django.db.models import Q
from restlib2.http import codes
from restlib2.params import Parametizer, StrParam, BoolParam, IntParam
from modeltree.tree import MODELTREE_DEFAULT_ALIAS, trees
from avocado.events import usage
from avocado.models import DataField
from avocado.query import pipeline
from avocado.stats import kmeans
from .base import FieldBase


MINIMUM_OBSERVATIONS = 500
MAXIMUM_OBSERVATIONS = 50000


class FieldDistParametizer(Parametizer):
    aware = BoolParam(False)
    cluster = BoolParam(True)
    n = IntParam()
    nulls = BoolParam(False)
    processor = StrParam('default', choices=pipeline.query_processors)
    sort = StrParam()
    tree = StrParam(MODELTREE_DEFAULT_ALIAS, choices=trees)


class FieldDistribution(FieldBase):
    "Field Counts Resource"

    parametizer = FieldDistParametizer

    def get(self, request, pk):
        instance = self.get_object(request, pk=pk)
        params = self.get_params(request)

        tree = trees[params.get('tree')]
        opts = tree.root_model._meta
        tree_field = DataField(
            app_name=opts.app_label, model_name=opts.module_name,
            field_name=opts.pk.name)

        # This will eventually make it's way in the parametizer, but lists
        # are not supported
        dimensions = request.GET.getlist('dimensions')

        if params['aware']:
            context = self.get_context(request)
        else:
            context = None

        QueryProcessor = pipeline.query_processors[params['processor']]
        processor = QueryProcessor(context=context, tree=tree)

        queryset = processor.get_queryset(request=request)

        # Explicit fields to group by, ignore ones that dont exist or the
        # user does not have permission to view. Default is to group by the
        # reference field for distinct counts.
        if any(dimensions):
            fields = []
            groupby = []

            for pk in dimensions:
                f = self.get_object(request, pk=pk)
                if f:
                    fields.append(f)
                    groupby.append(tree.query_string_for_field(f.field,
                                                               model=f.model))
        else:
            fields = [instance]
            groupby = [tree.query_string_for_field(instance.field,
                                                   model=instance.model)]

        # Perform a count aggregation of the tree model grouped by the
        # specified dimensions
        stats = tree_field.count(*groupby)

        # Apply it relative to the queryset
        stats = stats.apply(queryset)

        # Exclude null values. Dependending on the downstream use of the data,
        # nulls may or may not be desirable.
        if not params['nulls']:
            q = Q()
            for field in groupby:
                q = q | Q(**{field: None})
            stats = stats.exclude(q)

        # Begin constructing the response
        resp = {
            'data': [],
            'outliers': [],
            'clustered': False,
            'size': 0,
        }

        # Evaluate list of points
        length = len(stats)

        # Nothing to do
        if not length:
            usage.log('dist', instance=instance, request=request, data={
                'size': 0,
                'clustered': False,
                'aware': params['aware'],
            })
            return resp

        if length > MAXIMUM_OBSERVATIONS:
            data = {
                'message': 'Data too large',
            }
            return self.render(request, data,
                               status=codes.unprocessable_entity)

        # Apply ordering. If any of the fields are enumerable, ordering should
        # be relative to those fields. For continuous data, the ordering is
        # relative to the count of each group
        if (any([d.enumerable for d in fields]) and
                not params['sort'] == 'count'):
            stats = stats.order_by(*groupby)
        else:
            stats = stats.order_by('-count')

        clustered = False
        points = list(stats)
        outliers = []

        # For N-dimensional continuous data, check if clustering should occur
        # to down-sample the data.
        if all([d.simple_type == 'number' for d in fields]):
            # Extract observations for clustering
            obs = []
            for point in points:
                for i, dim in enumerate(point['values']):
                    if isinstance(dim, Decimal):
                        point['values'][i] = float(str(dim))
                obs.append(point['values'])

            # Perform k-means clustering. Determine centroids and calculate
            # the weighted count relatives to the centroid and observations
            # within the kmeans module.
            if params['cluster'] and length >= MINIMUM_OBSERVATIONS:
                clustered = True

                counts = [p['count'] for p in points]
                points, outliers = kmeans.weighted_counts(
                    obs, counts, params['n'])
            else:
                indexes = kmeans.find_outliers(obs, normalized=False)

                outliers = []
                for idx in indexes:
                    outliers.append(points[idx])
                    points[idx] = None
                points = [p for p in points if p is not None]

        usage.log('dist', instance=instance, request=request, data={
            'size': length,
            'clustered': clustered,
            'aware': params['aware'],
        })

        return {
            'data': points,
            'clustered': clustered,
            'outliers': outliers,
            'size': length,
        }
