import json
from django.contrib.auth.models import User
from django.contrib.contenttypes.models import ContentType
from django.test import Client
from avocado import history
from avocado.history.models import Revision
from avocado.models import DataView
from .base import AuthenticatedBaseTestCase


class ViewResourceTestCase(AuthenticatedBaseTestCase):
    def test_get_all(self):
        response = self.client.get('/api/views/',
            HTTP_ACCEPT='application/json')
        self.assertFalse(json.loads(response.content))

    def test_get_all_default(self):
        view = DataView(template=True, default=True, json={})
        view.save()
        response = self.client.get('/api/views/',
            HTTP_ACCEPT='application/json')
        self.assertEqual(len(json.loads(response.content)), 1)

    def test_get(self):
        view = DataView(user=self.user)
        view.save()
        response = self.client.get('/api/views/1/',
            HTTP_ACCEPT='application/json')
        self.assertEqual(response.status_code, 200)
        self.assertTrue(response.content)
        self.assertLess(view.accessed,
                DataView.objects.get(pk=view.pk).accessed)


class ViewsRevisionsResourceTestCase(AuthenticatedBaseTestCase):
    def test_get(self):
        view = DataView(user=self.user)
        view.save()

        response = self.client.get('/api/views/revisions/',
            HTTP_ACCEPT='application/json')
        self.assertEqual(response.status_code, 200)
        self.assertEqual(len(json.loads(response.content)), 1)

    def test_user(self):
        view = DataView(user=self.user)
        view.save()

        user2 = User.objects.create_user(username='FAKE', password='ALSO_FAKE')
        view2 = DataView(user=user2)
        view2.save()

        self.assertEqual(Revision.objects.filter(
            content_type=ContentType.objects.get_for_model(DataView)).count(), 2)

        response = self.client.get('/api/views/revisions/',
            HTTP_ACCEPT='application/json')
        self.assertEqual(response.status_code, 200)
        self.assertEqual(len(json.loads(response.content)), 1)

    def test_session(self):
        # This session mumbo-jumbo is from:
        #       https://code.djangoproject.com/ticket/10899
        self.client = Client()
        from django.conf import settings
        from django.utils.importlib import import_module
        engine = import_module(settings.SESSION_ENGINE)
        store = engine.SessionStore()
        store.save()  # we need to make load() work, or the cookie is worthless
        session_key = store.session_key
        self.client.cookies[settings.SESSION_COOKIE_NAME] = session_key

        view = DataView(session_key=self.client.session.session_key)
        view.save()

        view2 = DataView(session_key='XYZ')
        view2.save()

        self.assertEqual(Revision.objects.filter(
            content_type=ContentType.objects.get_for_model(DataView)).count(), 2)

        response = self.client.get('/api/views/revisions/',
            HTTP_ACCEPT='application/json')
        self.assertEqual(response.status_code, 200)
        self.assertEqual(len(json.loads(response.content)), 1)

    def test_no_identifier(self):
        view = DataView()
        view.save()

        # Make sure the revision was created but has nothing useful in
        # either of the "owner" properties.
        self.assertEqual(Revision.objects.filter(
            content_type=ContentType.objects.get_for_model(DataView),
            user=None, session_key=None).count(), 1)

        # We want this request to come from an anonymous user
        self.client.logout()

        response = self.client.get('/api/views/revisions/',
            HTTP_ACCEPT='application/json')
        self.assertEqual(response.status_code, 200)
        self.assertEqual(len(json.loads(response.content)), 0)

    def test_embedded(self):
        view = DataView(user=self.user, name='My View',
            description='This is not a descriptive description')
        view.save()

        # Retrieve the revisions the normal way and make sure the object
        # itself is not included.
        response = self.client.get('/api/views/revisions/',
            HTTP_ACCEPT='application/json')
        self.assertEqual(response.status_code, 200)
        self.assertEqual(len(json.loads(response.content)), 1)
        no_embed_revision = json.loads(response.content)[0]
        self.assertFalse('object' in no_embed_revision)

        # Now retrieve the revisiosn with the embed flag enabled and verify
        # that the object is now included with the revision.
        response = self.client.get('/api/views/revisions/', {'embed': True},
            HTTP_ACCEPT='application/json')
        self.assertEqual(response.status_code, 200)
        self.assertEqual(len(json.loads(response.content)), 1)
        embed_revision = json.loads(response.content)[0]
        self.assertTrue('object' in embed_revision)

        # Make sure the included object matches the copy of the object directly
        # from the object resource itself.
        response = self.client.get('/api/views/1/',
            HTTP_ACCEPT='application/json')
        self.assertEqual(response.status_code, 200)
        revision_view = json.loads(response.content)

        # We can't just compare the objects directly to one another because the
        # object returned from the call to /api/views/1/ will have '_links'
        # while the embeded object will not because the link location is
        # different for Revisions.
        for key in embed_revision['object']:
            self.assertEqual(revision_view[key], embed_revision['object'][key])


class ViewRevisionsResourceTestCase(AuthenticatedBaseTestCase):
    def test_get(self):
        view = DataView(user=self.user)
        view.save()

        view.name = "Fake name"
        view.save()

        view.description = "Terribly vague description"
        view.save()

        url = '/api/views/{0}/revisions/'.format(view.id)

        response = self.client.get(url,
            HTTP_ACCEPT='application/json')
        self.assertEqual(response.status_code, 200)
        self.assertEqual(len(json.loads(response.content)), 3)


class ViewRevisionResourceTestCase(AuthenticatedBaseTestCase):
    def test_get(self):
        view = DataView(user=self.user)
        view.save()

        view.name = "Fake name"
        view.save()

        target_revision_id = Revision.objects.all().count()

        view.description = "Terribly vague description"
        view.save()

        url = '/api/views/{0}/revisions/{1}/'.format(view.id, target_revision_id)

        response = self.client.get(url,
            HTTP_ACCEPT='application/json')
        self.assertEqual(response.status_code, 200)
        self.assertTrue(response.content)

        revision = json.loads(response.content)
        self.assertEqual(revision['changes'], {
            'name': {
                'old_value': None,
                'new_value': 'Fake name'
             }
        })
        self.assertFalse("description" in revision['changes'])

    def test_non_existent_object(self):
        view = DataView(user=self.user)
        view.save()

        view.name = "Fake name"
        view.save()

        target_revision_id = Revision.objects.all().count()

        url = '/api/views/{0}/revisions/{1}/'.format(123456789, target_revision_id)

        response = self.client.get(url,
            HTTP_ACCEPT='application/json')
        self.assertEqual(response.status_code, 404)

    def test_non_existent_revision(self):
        view = DataView(user=self.user)
        view.save()

        view.name = "Fake name"
        view.save()

        url = '/api/views/{0}/revisions/{1}/'.format(view.id, 123456789)

        response = self.client.get(url,
            HTTP_ACCEPT='application/json')
        self.assertEqual(response.status_code, 404)