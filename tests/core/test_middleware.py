"""
Tests for core middleware.
"""
import pytest
from django.test import RequestFactory, override_settings
from django.contrib.auth.models import AnonymousUser
from unittest.mock import Mock, patch, MagicMock

from apps.core.middleware.language import APILanguageMiddleware
from apps.core.middleware.tenant import TenantMiddleware, get_current_restaurant, require_restaurant


class MockResponse(dict):
    """Mock response that supports both attribute and item access."""
    def __init__(self, status_code=200):
        super().__init__()
        self.status_code = status_code


@pytest.mark.django_db
class TestAPILanguageMiddleware:
    """Tests for APILanguageMiddleware."""

    def setup_method(self):
        """Set up test fixtures."""
        self.factory = RequestFactory()
        self.get_response = Mock(return_value=MockResponse(status_code=200))
        self.middleware = APILanguageMiddleware(self.get_response)

    def test_query_param_takes_priority(self, user):
        """Test language from query parameter has highest priority."""
        request = self.factory.get('/?lang=en')
        request.user = user
        user.preferred_language = 'ru'

        response = self.middleware(request)

        assert request.LANGUAGE_CODE == 'en'
        assert response['Content-Language'] == 'en'

    def test_user_preference_when_no_query_param(self, user):
        """Test user preference is used when no query parameter."""
        request = self.factory.get('/')
        request.user = user
        user.preferred_language = 'ru'

        self.middleware(request)

        assert request.LANGUAGE_CODE == 'ru'

    def test_accept_language_header(self):
        """Test Accept-Language header parsing."""
        request = self.factory.get('/', HTTP_ACCEPT_LANGUAGE='en-US,en;q=0.9,ka;q=0.8')
        request.user = AnonymousUser()

        self.middleware(request)

        assert request.LANGUAGE_CODE == 'en'

    def test_accept_language_header_with_quality(self):
        """Test Accept-Language header respects quality values."""
        request = self.factory.get('/', HTTP_ACCEPT_LANGUAGE='ru;q=0.5,ka;q=0.9,en;q=0.7')
        request.user = AnonymousUser()

        self.middleware(request)

        assert request.LANGUAGE_CODE == 'ka'

    def test_default_language_fallback(self):
        """Test fallback to default language (Georgian)."""
        request = self.factory.get('/')
        request.user = AnonymousUser()

        self.middleware(request)

        assert request.LANGUAGE_CODE == 'ka'

    def test_unsupported_language_falls_back(self):
        """Test unsupported language falls back to default."""
        request = self.factory.get('/?lang=fr')
        request.user = AnonymousUser()

        self.middleware(request)

        assert request.LANGUAGE_CODE == 'ka'

    def test_content_language_header_set(self):
        """Test Content-Language header is set on response."""
        request = self.factory.get('/?lang=en')
        request.user = AnonymousUser()

        response = self.middleware(request)

        assert response['Content-Language'] == 'en'

    def test_malformed_accept_language_header(self):
        """Test handling of malformed Accept-Language header."""
        request = self.factory.get('/', HTTP_ACCEPT_LANGUAGE='invalid;;;')
        request.user = AnonymousUser()

        self.middleware(request)

        assert request.LANGUAGE_CODE == 'ka'

    def test_empty_accept_language_header(self):
        """Test handling of empty Accept-Language header."""
        request = self.factory.get('/', HTTP_ACCEPT_LANGUAGE='')
        request.user = AnonymousUser()

        self.middleware(request)

        assert request.LANGUAGE_CODE == 'ka'


@pytest.mark.django_db
class TestTenantMiddleware:
    """Tests for TenantMiddleware."""

    def setup_method(self):
        """Set up test fixtures."""
        self.factory = RequestFactory()
        self.get_response = Mock(return_value=MockResponse(status_code=200))
        self.middleware = TenantMiddleware(self.get_response)

    @override_settings(MAIN_DOMAIN='example.com', ALLOWED_HOSTS=['*'])
    def test_no_subdomain_sets_null_restaurant(self):
        """Test request without subdomain has no restaurant."""
        request = self.factory.get('/', SERVER_NAME='example.com')

        self.middleware(request)

        assert request.restaurant is None
        assert request.is_dashboard is False

    @override_settings(MAIN_DOMAIN='example.com', ALLOWED_HOSTS=['*'])
    def test_excluded_subdomain_sets_null_restaurant(self):
        """Test excluded subdomains (www, api, etc.) have no restaurant."""
        for subdomain in ['www', 'api', 'admin', 'static', 'media']:
            request = self.factory.get('/', SERVER_NAME=f'{subdomain}.example.com')

            self.middleware(request)

            assert request.restaurant is None
            assert request.is_dashboard is False

    @override_settings(MAIN_DOMAIN='example.com', ALLOWED_HOSTS=['*'])
    def test_valid_subdomain_sets_restaurant(self):
        """Test valid subdomain sets restaurant on request."""
        mock_restaurant = MagicMock()
        mock_restaurant.slug = 'testrestaurant'
        mock_restaurant.is_active = True

        mock_restaurant_class = MagicMock()
        mock_restaurant_class.objects.get.return_value = mock_restaurant

        # Patch where the import happens in the middleware __call__ method
        with patch.dict('sys.modules', {'apps.tenants.models': MagicMock(Restaurant=mock_restaurant_class)}):
            # Force re-import
            import importlib
            import apps.core.middleware.tenant as tenant_module
            importlib.reload(tenant_module)

            middleware = TenantMiddleware(self.get_response)
            request = self.factory.get('/', SERVER_NAME='testrestaurant.example.com')
            middleware(request)

            assert request.restaurant == mock_restaurant
            assert request.is_dashboard is True

    @override_settings(MAIN_DOMAIN='example.com', ALLOWED_HOSTS=['*'])
    def test_nonexistent_restaurant_sets_null(self):
        """Test nonexistent restaurant slug sets null."""
        from apps.tenants.models import Restaurant

        # Just use the real model with a non-existent slug
        request = self.factory.get('/', SERVER_NAME='nonexistent.example.com')
        self.middleware(request)

        assert request.restaurant is None
        assert request.is_dashboard is False

    @override_settings(MAIN_DOMAIN='example.com', ALLOWED_HOSTS=['*'])
    def test_port_is_stripped_from_host(self):
        """Test port number is stripped from host."""
        request = self.factory.get('/', SERVER_NAME='example.com', SERVER_PORT='8000')

        self.middleware(request)

        assert request.restaurant is None  # No subdomain
        assert request.is_dashboard is False


class TestGetCurrentRestaurant:
    """Tests for get_current_restaurant helper."""

    def test_returns_restaurant_when_present(self):
        """Test returns restaurant when set on request."""
        request = Mock()
        request.restaurant = Mock()

        result = get_current_restaurant(request)

        assert result == request.restaurant

    def test_returns_none_when_not_present(self):
        """Test returns None when no restaurant attribute."""
        request = Mock(spec=[])  # No attributes

        result = get_current_restaurant(request)

        assert result is None


class TestRequireRestaurantDecorator:
    """Tests for require_restaurant decorator."""

    def test_passes_when_restaurant_present(self):
        """Test decorated view executes when restaurant is set."""
        @require_restaurant
        def test_view(request):
            return 'success'

        request = Mock()
        request.restaurant = Mock()

        result = test_view(request)

        assert result == 'success'

    def test_raises_404_when_no_restaurant(self):
        """Test decorated view raises 404 when no restaurant."""
        from django.http import Http404

        @require_restaurant
        def test_view(request):
            return 'success'

        request = Mock()
        request.restaurant = None

        with pytest.raises(Http404):
            test_view(request)
