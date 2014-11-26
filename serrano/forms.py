from __future__ import unicode_literals

import logging
from django import forms
from django.conf import settings as django_settings
from django.contrib.auth.models import User
from django.contrib.sites.models import get_current_site
from django.core.exceptions import ValidationError
from django.core.urlresolvers import reverse, NoReverseMatch
from django.core.validators import validate_email
from modeltree.tree import MODELTREE_DEFAULT_ALIAS
from avocado.models import DataContext, DataView, DataQuery
from avocado.query import pipeline
from serrano import utils
from serrano.conf import settings

log = logging.getLogger(__name__)

SHARED_QUERY_EMAIL_TITLE = '{site_name}: {query_name} has been shared with '\
                           'you!'
SHARED_QUERY_EMAIL_BODY = 'View the query at {query_url}'


class ContextForm(forms.ModelForm):
    def __init__(self, request, *args, **kwargs):
        self.request = request
        self.count_needs_update = kwargs.pop('force_count', None)
        self.processor = kwargs.pop('processor', 'default')
        self.tree = kwargs.pop('tree', MODELTREE_DEFAULT_ALIAS)

        super(ContextForm, self).__init__(*args, **kwargs)

    def clean_json(self):
        json = self.cleaned_data.get('json')

        if self.count_needs_update is None and self.instance:
            existing = self.instance.json

            if (existing or json and existing != json or json and
                    self.instance.count is None):
                self.count_needs_update = True
            else:
                self.count_needs_update = False
        return json

    def save(self, commit=True):
        instance = super(ContextForm, self).save(commit=False)
        request = self.request

        if getattr(request, 'user', None) and request.user.is_authenticated():
            instance.user = request.user
        else:
            instance.session_key = request.session.session_key

        QueryProcessor = pipeline.query_processors[self.processor]
        processor = QueryProcessor(tree=self.tree)
        queryset = processor.get_queryset(request=request)

        # Only recalculated count if conditions exist. This is to
        # prevent re-counting the entire dataset. An alternative
        # solution may be desirable such as pre-computing and
        # caching the count ahead of time.
        if self.count_needs_update:
            instance.count = \
                instance.apply(queryset=queryset).distinct().count()
            self.count_needs_update = False
        else:
            instance.count = None

        if commit:
            instance.save()

        return instance

    class Meta(object):
        model = DataContext
        fields = ('name', 'description', 'keywords', 'json', 'session')


class ViewForm(forms.ModelForm):
    def __init__(self, request, *args, **kwargs):
        self.request = request
        super(ViewForm, self).__init__(*args, **kwargs)

    def save(self, commit=True):
        instance = super(ViewForm, self).save(commit=False)
        request = self.request

        if getattr(request, 'user', None) and request.user.is_authenticated():
            instance.user = request.user
        else:
            instance.session_key = request.session.session_key

        if commit:
            instance.save()

        return instance

    class Meta(object):
        model = DataView
        fields = ('name', 'description', 'keywords', 'json', 'session')


class QueryForm(forms.ModelForm):
    # A list of the usernames or email addresses of the User's who the query
    # should be shared with. This is a string where each email/username is
    # separated by a ','.
    usernames_or_emails = forms.CharField(widget=forms.Textarea,
                                          required=False)
    message = forms.CharField(widget=forms.Textarea, required=False)

    def __init__(self, request, *args, **kwargs):
        self.request = request
        self.count_needs_update_context = kwargs.pop('force_count', None)
        self.count_needs_update_view = self.count_needs_update_context
        super(QueryForm, self).__init__(*args, **kwargs)

    def clean_context_json(self):
        json = self.cleaned_data.get('context_json')
        if self.count_needs_update_context is None:
            existing = self.instance.context_json
            if (existing or json and existing != json or json and
                    self.instance.count is None):
                self.count_needs_update_context = True
            else:
                self.count_needs_update_context = False
        return json

    def clean_view_json(self):
        json = self.cleaned_data.get('view_json')
        if self.count_needs_update_view is None:
            existing = self.instance.view_json
            if (existing or json and existing != json or json and
                    self.instance.count is None):
                self.count_needs_update_view = True
            else:
                self.count_needs_update_view = False
        return json

    def clean_usernames_or_emails(self):
        """
        Cleans and validates the list of usernames and email address. This
        method returns a list of email addresses containing the valid emails
        and emails of valid users in the cleaned_data value for the
        usernames_or_emails field.
        """
        user_labels = self.cleaned_data.get('usernames_or_emails')
        emails = set()
        for label in user_labels.split(','):
            # Remove whitespace from the label, there should not be whitespace
            # in usernames or email addresses. This use of split is somewhat
            # non-obvious, see the link below:
            #       http://docs.python.org/2/library/stdtypes.html#str.split
            label = "".join(label.split())

            if not label:
                continue

            try:
                validate_email(label)
                emails.add(label)
            except ValidationError:
                # If this user lookup label is not an email address, try to
                # find the user with the supplied username and get the
                # email that way. If no user with this username is found
                # then give up since we only support email and username
                # lookups.
                try:
                    user = User.objects.only('email').get(username=label)
                    emails.add(user.email)
                except User.DoesNotExist:
                    log.warning("Unable to share query with '{0}'. It is not "
                                "a valid email or username.".format(label))

        return emails

    def save(self, commit=True):
        instance = super(QueryForm, self).save(commit=False)
        request = self.request

        if getattr(request, 'user', None) and request.user.is_authenticated():
            instance.user = request.user
        else:
            instance.session_key = request.session.session_key

        # Only recalculated count if conditions exist. This is to
        # prevent re-counting the entire dataset. An alternative
        # solution may be desirable such as pre-computing and
        # caching the count ahead of time.
        if self.count_needs_update_context:
            instance.distinct_count = instance.apply().distinct().count()
            self.count_needs_update_context = False
        else:
            instance.distinct_count = None

        if self.count_needs_update_view:
            instance.record_count = instance.apply().count()
            self.count_needs_update_view = False
        else:
            instance.record_count = None

        if commit:
            instance.save()

            script_name = getattr(django_settings, 'SCRIPT_NAME', '')

            # The code to update the shared_users field on the Query model
            # included inside this if statement because the shared_users
            # field in inaccessible until the instance is saved which is only
            # done in the case of commit being True. Using commit=False when
            # saving the super class was not enough. That is the reason for
            # this being embedded within the commit if and for the explicit
            # save_m2m call below.
            all_emails = self.cleaned_data.get('usernames_or_emails')

            # Get the list of existing email addresses for users this query is
            # already shared with. We only want to email users the first time
            # a query is shared with them so we get the existing list of email
            # addresses to avoid repeated emails to users about the same query.
            existing_emails = set(instance.shared_users.all().values_list(
                'email', flat=True))
            new_emails = all_emails - existing_emails

            site = get_current_site(request)

            try:
                site_url = request.build_absolute_uri(script_name + '/')
            except KeyError:
                site_url = site.domain + script_name

            # Use the site url as the default query url in case there are
            # issues generating the query url.
            query_url = site_url

            reverse_name = settings.QUERY_REVERSE_NAME

            if reverse_name:
                try:
                    query_url = reverse(reverse_name,
                                        kwargs={'pk': instance.pk})

                    # Since reverse will just return the path to the query
                    # we need to prepend the site url to make it a valid
                    # link that people can follow.
                    try:
                        query_url = request.build_absolute_uri(query_url)
                    except KeyError:
                        query_url = site.domain + script_name + query_url
                except NoReverseMatch:
                    log.warn("Could not reverse '{0}'. ".format(reverse_name))
            else:
                log.warn('SERRANO_QUERY_REVERSE_NAME not found in settings.')

            title = SHARED_QUERY_EMAIL_TITLE.format(query_name=instance.name,
                                                    site_name=site.name)

            body = SHARED_QUERY_EMAIL_BODY.format(query_url=query_url)

            if self.cleaned_data.get('message'):
                body = '{0}\n\n--\n{1}'.format(
                    self.cleaned_data.get('message'), body)

            # Email and register all the new email addresses
            utils.send_mail(new_emails, title, body)

            for email in new_emails:
                instance.share_with_user(email)

            # Find and remove users who have had their query share revoked
            removed_emails = existing_emails - all_emails

            for user in User.objects.filter(email__in=removed_emails):
                instance.shared_users.remove(user)

            self.save_m2m()

        return instance

    class Meta(object):
        model = DataQuery
        fields = ('name', 'description', 'keywords', 'context_json',
                  'view_json', 'session', 'public')
