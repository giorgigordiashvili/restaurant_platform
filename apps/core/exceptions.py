"""
Custom exception handlers and exceptions.
"""
import logging

from django.core.exceptions import ValidationError as DjangoValidationError
from django.http import Http404
from rest_framework import status
from rest_framework.exceptions import APIException, ValidationError
from rest_framework.response import Response
from rest_framework.views import exception_handler

logger = logging.getLogger(__name__)


def custom_exception_handler(exc, context):
    """
    Custom exception handler that formats all errors consistently.
    """
    # Call REST framework's default exception handler first
    response = exception_handler(exc, context)

    if response is not None:
        # Customize the response data
        custom_response_data = {
            'success': False,
            'error': {
                'code': get_error_code(exc),
                'message': get_error_message(exc, response),
                'details': get_error_details(response.data),
            }
        }
        response.data = custom_response_data
    else:
        # Handle non-API exceptions
        if isinstance(exc, DjangoValidationError):
            data = {
                'success': False,
                'error': {
                    'code': 'validation_error',
                    'message': 'Validation error',
                    'details': exc.messages if hasattr(exc, 'messages') else [str(exc)],
                }
            }
            response = Response(data, status=status.HTTP_400_BAD_REQUEST)
        elif isinstance(exc, Http404):
            data = {
                'success': False,
                'error': {
                    'code': 'not_found',
                    'message': str(exc) if str(exc) != 'Http404' else 'Resource not found',
                    'details': None,
                }
            }
            response = Response(data, status=status.HTTP_404_NOT_FOUND)
        else:
            # Log unexpected exceptions
            logger.exception(f"Unhandled exception: {exc}")
            data = {
                'success': False,
                'error': {
                    'code': 'server_error',
                    'message': 'An unexpected error occurred',
                    'details': None,
                }
            }
            response = Response(data, status=status.HTTP_500_INTERNAL_SERVER_ERROR)

    return response


def get_error_code(exc):
    """Get error code from exception."""
    if hasattr(exc, 'default_code'):
        return exc.default_code
    return type(exc).__name__.lower()


def get_error_message(exc, response):
    """Get human-readable error message."""
    if hasattr(exc, 'detail'):
        if isinstance(exc.detail, str):
            return exc.detail
        elif isinstance(exc.detail, dict) and 'detail' in exc.detail:
            return exc.detail['detail']
    return response.status_text


def get_error_details(data):
    """Format error details consistently."""
    if isinstance(data, dict):
        if 'detail' in data:
            return None
        return data
    elif isinstance(data, list):
        return data
    return None


# Custom Exception Classes

class BusinessLogicError(APIException):
    """Base exception for business logic errors."""
    status_code = status.HTTP_400_BAD_REQUEST
    default_code = 'business_logic_error'
    default_detail = 'A business logic error occurred.'


class ResourceNotFoundError(APIException):
    """Exception for when a resource is not found."""
    status_code = status.HTTP_404_NOT_FOUND
    default_code = 'not_found'
    default_detail = 'The requested resource was not found.'


class PermissionDeniedError(APIException):
    """Exception for permission denied errors."""
    status_code = status.HTTP_403_FORBIDDEN
    default_code = 'permission_denied'
    default_detail = 'You do not have permission to perform this action.'


class ConflictError(APIException):
    """Exception for conflict errors (e.g., duplicate resources)."""
    status_code = status.HTTP_409_CONFLICT
    default_code = 'conflict'
    default_detail = 'A conflict occurred with the current state of the resource.'


class RateLimitExceededError(APIException):
    """Exception for rate limit exceeded."""
    status_code = status.HTTP_429_TOO_MANY_REQUESTS
    default_code = 'rate_limit_exceeded'
    default_detail = 'Rate limit exceeded. Please try again later.'


class OrderError(BusinessLogicError):
    """Exception for order-related errors."""
    default_code = 'order_error'
    default_detail = 'An error occurred while processing the order.'


class ReservationError(BusinessLogicError):
    """Exception for reservation-related errors."""
    default_code = 'reservation_error'
    default_detail = 'An error occurred while processing the reservation.'


class PaymentError(BusinessLogicError):
    """Exception for payment-related errors."""
    default_code = 'payment_error'
    default_detail = 'An error occurred while processing the payment.'


class RestaurantNotActiveError(APIException):
    """Exception when restaurant is not active."""
    status_code = status.HTTP_403_FORBIDDEN
    default_code = 'restaurant_not_active'
    default_detail = 'This restaurant is not currently accepting orders.'
