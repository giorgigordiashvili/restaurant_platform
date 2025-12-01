"""
Custom throttle classes for rate limiting.
"""
from rest_framework.throttling import SimpleRateThrottle


class BurstRateThrottle(SimpleRateThrottle):
    """
    Throttle to prevent burst attacks (many requests in short time).
    """
    scope = 'burst'

    def get_cache_key(self, request, view):
        if request.user.is_authenticated:
            ident = str(request.user.pk)
        else:
            ident = self.get_ident(request)

        return self.cache_format % {
            'scope': self.scope,
            'ident': ident
        }


class AuthRateThrottle(SimpleRateThrottle):
    """
    Strict rate limiting for authentication endpoints.
    Prevents brute force attacks on login.
    """
    scope = 'auth'

    def get_cache_key(self, request, view):
        # Rate limit by IP for auth endpoints
        return self.cache_format % {
            'scope': self.scope,
            'ident': self.get_ident(request)
        }


class PasswordResetThrottle(SimpleRateThrottle):
    """
    Rate limiting for password reset requests.
    Prevents email enumeration and spam.
    """
    scope = 'password_reset'
    rate = '3/hour'

    def get_cache_key(self, request, view):
        # Rate limit by IP
        return self.cache_format % {
            'scope': self.scope,
            'ident': self.get_ident(request)
        }


class OrderCreationThrottle(SimpleRateThrottle):
    """
    Rate limiting for order creation.
    Prevents order spam.
    """
    scope = 'order_creation'
    rate = '30/hour'

    def get_cache_key(self, request, view):
        if request.user.is_authenticated:
            ident = str(request.user.pk)
        else:
            ident = self.get_ident(request)

        return self.cache_format % {
            'scope': self.scope,
            'ident': ident
        }


class SMSThrottle(SimpleRateThrottle):
    """
    Rate limiting for SMS sending (phone verification).
    """
    scope = 'sms'
    rate = '5/hour'

    def get_cache_key(self, request, view):
        return self.cache_format % {
            'scope': self.scope,
            'ident': self.get_ident(request)
        }
