# pylint: disable=no-self-use
from collections import namedtuple

from mock import patch, Mock, MagicMock
import pytest

import deform
from pyramid import httpexceptions
from pyramid.testing import DummyRequest
from horus.interfaces import (
    IActivationClass,
    IProfileForm,
    IProfileSchema,
    IRegisterForm,
    IRegisterSchema,
    IUIStrings,
    IUserClass,
)
from horus.schemas import ProfileSchema
from horus.forms import SubmitForm
from horus.strings import UIStringsBase

from h.accounts import schemas
from h.accounts import views
from h.accounts.views import validate_form
from h.accounts.views import RegisterController
from h.accounts.views import ProfileController
from h.accounts.views import AsyncFormViewMapper
from h.models import _


class FakeUser(object):
    def __init__(self, **kwargs):
        for k in kwargs:
            setattr(self, k, kwargs[k])


def configure(config):
    config.registry.registerUtility(UIStringsBase, IUIStrings)
    config.registry.registerUtility(ProfileSchema, IProfileSchema)
    config.registry.registerUtility(SubmitForm, IProfileForm)
    config.registry.registerUtility(MagicMock(), IRegisterSchema)
    config.registry.registerUtility(MagicMock(), IRegisterForm)
    config.registry.feature = MagicMock()
    config.registry.feature.return_value = None


def _get_fake_request(username, password):
    fake_request = DummyRequest()

    def get_fake_token():
        return 'fake_token'

    fake_request.method = 'POST'
    fake_request.params['csrf_token'] = 'fake_token'
    fake_request.session.get_csrf_token = get_fake_token
    fake_request.POST['username'] = username
    fake_request.POST['pwd'] = password

    return fake_request


# A fake version of colander.Invalid for use when testing validate_form
FakeInvalid = namedtuple('FakeInvalid', 'children')


def test_validate_form_passes_data_to_validate():
    idata = {}
    form = MagicMock()

    err, data = validate_form(form, idata)

    form.validate.assert_called_with(idata)


def test_validate_form_failure():
    invalid = FakeInvalid(children=object())
    form = MagicMock()
    form.validate.side_effect = deform.ValidationFailure(None, None, invalid)

    err, data = validate_form(form, {})

    assert err == {'errors': invalid.children}
    assert data is None


def test_validate_form_ok():
    form = MagicMock()
    form.validate.return_value = {'foo': 'bar'}

    err, odata = validate_form(form, {})

    assert err is None
    assert odata == {'foo': 'bar'}


@pytest.mark.usefixtures('activation_model', 'dummy_db_session')
def test_profile_returns_email(config, user_model, authn_policy):
    """profile() should include the user's email in the dict it returns."""
    request = _get_fake_request("john", "doe")
    authn_policy.authenticated_userid.return_value = "john"
    user_model.get_by_id.return_value = FakeUser(
        email="test_user@test_email.com")
    configure(config)

    profile = ProfileController(request).profile()

    assert profile["model"]["email"] == "test_user@test_email.com"


def test_edit_profile_invalid_password(authn_policy, form_validator, user_model):
    """Make sure our edit_profile call validates the user password."""
    authn_policy.authenticated_userid.return_value = "johndoe"
    form_validator.return_value = (None, {
        "username": "john",
        "pwd": "blah",
        "subscriptions": "",
    })

    # Mock an invalid password
    user_model.validate_user.return_value = False

    request = DummyRequest(method='POST')
    profile = ProfileController(request)
    result = profile.edit_profile()

    assert result['code'] == 401
    assert any('pwd' in err for err in result['errors'])


def test_edit_profile_with_validation_failure(authn_policy, form_validator):
    """If form validation fails, return the error object."""
    authn_policy.authenticated_userid.return_value = "johndoe"
    form_validator.return_value = ({"errors": "BOOM!"}, None)

    request = DummyRequest(method='POST')
    profile = ProfileController(request)
    result = profile.edit_profile()

    assert result == {"errors": "BOOM!"}


def test_edit_profile_successfully(authn_policy, form_validator, user_model):
    """edit_profile() returns a dict with key "form" when successful."""
    authn_policy.authenticated_userid.return_value = "johndoe"
    form_validator.return_value = (None, {
        "username": "johndoe",
        "pwd": "password",
        "subscriptions": "",
    })
    user_model.validate_user.return_value = True
    user_model.get_by_id.return_value = FakeUser(email="john@doe.com")

    request = DummyRequest(method='POST')
    profile = ProfileController(request)
    result = profile.edit_profile()

    assert result == {"model": {"email": "john@doe.com"}}


def test_subscription_update(authn_policy, form_validator,
                             subscriptions_model, user_model):
    """Make sure that the new status is written into the DB."""
    authn_policy.authenticated_userid.return_value = "acct:john@doe"
    form_validator.return_value = (None, {
        "username": "acct:john@doe",
        "pwd": "smith",
        "subscriptions": '{"active":true,"uri":"acct:john@doe","id":1}',
    })
    mock_sub = Mock(active=False, uri="acct:john@doe")
    subscriptions_model.get_by_id.return_value = mock_sub
    user_model.get_by_id.return_value = FakeUser(email="john@doe")

    request = DummyRequest(method='POST')
    profile = ProfileController(request)
    result = profile.edit_profile()

    assert mock_sub.active == True
    assert result == {"model": {"email": "john@doe"}}


def test_asyncformviewmapper_preserves_email_in_response():
    """AsyncFormViewMapper should preserve the email in the response.

    ProfileController.edit_profile() returns an HTTPFound with a JSON body
    containing a model dict with the user's email address in it.

    AsyncFormViewMapper should preserve this email address in the dict
    that it returns.

    """
    mapper = AsyncFormViewMapper(attr="edit_profile")

    class ViewController(object):

        def __init__(self, request):
            pass

        def edit_profile(self):
            response = httpexceptions.HTTPFound("fake url")
            response.json = {"model": {"email": "fake email"}}
            return response

    result = mapper(ViewController)({}, DummyRequest())

    assert result["model"]["email"] == "fake email"


@pytest.mark.usefixtures('activation_model',
                         'dummy_db_session')
def test_disable_invalid_password(config, form_validator, user_model):
    """
    Make sure our disable_user call validates the user password
    """
    request = _get_fake_request('john', 'doe')
    form_validator.return_value = (None, {"username": "john", "pwd": "doe"})
    configure(config)

    # With an invalid password, get_user returns None
    user_model.get_user.return_value = None

    profile = ProfileController(request)
    result = profile.disable_user()

    assert result['code'] == 401
    assert any('pwd' in err for err in result['errors'])


@pytest.mark.usefixtures('activation_model',
                         'dummy_db_session')
def test_user_disabled(config, form_validator, user_model):
    """
    Check if the user is disabled
    """
    request = _get_fake_request('john', 'doe')
    form_validator.return_value = (None, {"username": "john", "pwd": "doe"})
    configure(config)

    user = FakeUser(password='abc')
    user_model.get_user.return_value = user

    profile = ProfileController(request)
    profile.disable_user()

    assert user.password == user_model.generate_random_password.return_value


@pytest.mark.usefixtures('activation_model',
                         'dummy_db_session',
                         'mailer',
                         'routes_mapper',
                         'user_model')
def test_registration_does_not_autologin(config, authn_policy):
    configure(config)

    request = DummyRequest()
    request.method = 'POST'
    request.POST.update({'email': 'giraffe@example.com',
                         'password': 'secret',
                         'username': 'giraffe'})

    ctrl = RegisterController(request)
    ctrl.register()

    assert not authn_policy.remember.called


@pytest.fixture
def subscriptions_model(request):
    patcher = patch('h.accounts.views.Subscriptions', autospec=True)
    request.addfinalizer(patcher.stop)
    return patcher.start()


@pytest.fixture
def user_model(config, request):
    patcher = patch('h.accounts.views.User', autospec=True)
    request.addfinalizer(patcher.stop)
    user = patcher.start()
    config.registry.registerUtility(user, IUserClass)
    return user


@pytest.fixture
def activation_model(config):
    mock = MagicMock()
    config.registry.registerUtility(mock, IActivationClass)
    return mock


@pytest.fixture
def form_validator(request):
    patcher = patch('h.accounts.views.validate_form', autospec=True)
    request.addfinalizer(patcher.stop)
    return patcher.start()
