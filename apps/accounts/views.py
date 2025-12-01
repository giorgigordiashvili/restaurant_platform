"""
Views for the accounts app.
"""
from django.contrib.auth import logout
from rest_framework import status, generics
from rest_framework.permissions import AllowAny, IsAuthenticated
from rest_framework.response import Response
from rest_framework.views import APIView
from rest_framework_simplejwt.tokens import RefreshToken
from rest_framework_simplejwt.views import TokenObtainPairView, TokenRefreshView
from drf_spectacular.utils import extend_schema, extend_schema_view

from apps.core.throttling import AuthRateThrottle, PasswordResetThrottle
from .models import User
from .serializers import (
    UserSerializer,
    UserRegistrationSerializer,
    UserUpdateSerializer,
    ChangePasswordSerializer,
    PasswordResetRequestSerializer,
    PasswordResetConfirmSerializer,
    CustomTokenObtainPairSerializer,
)


@extend_schema(tags=['Auth'])
class RegisterView(generics.CreateAPIView):
    """
    Register a new user account.
    """
    queryset = User.objects.all()
    serializer_class = UserRegistrationSerializer
    permission_classes = [AllowAny]
    throttle_classes = [AuthRateThrottle]

    def create(self, request, *args, **kwargs):
        serializer = self.get_serializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        user = serializer.save()

        # Generate tokens
        refresh = RefreshToken.for_user(user)

        # Set audit action
        request._audit_action = 'user_create'
        request._audit_target_model = 'User'
        request._audit_target_id = str(user.id)

        return Response({
            'success': True,
            'message': 'Registration successful',
            'data': {
                'user': UserSerializer(user).data,
                'tokens': {
                    'refresh': str(refresh),
                    'access': str(refresh.access_token),
                }
            }
        }, status=status.HTTP_201_CREATED)


@extend_schema(tags=['Auth'])
class LoginView(TokenObtainPairView):
    """
    Authenticate user and return JWT tokens.
    """
    serializer_class = CustomTokenObtainPairSerializer
    throttle_classes = [AuthRateThrottle]

    def post(self, request, *args, **kwargs):
        response = super().post(request, *args, **kwargs)

        if response.status_code == 200:
            # Set audit action
            request._audit_action = 'login'
            request._audit_description = f"User logged in: {request.data.get('email')}"

            # Update last login IP
            try:
                user = User.objects.get(email__iexact=request.data.get('email'))
                ip = self._get_client_ip(request)
                user.update_last_login_ip(ip)
            except User.DoesNotExist:
                pass

        return response

    def _get_client_ip(self, request):
        x_forwarded_for = request.META.get('HTTP_X_FORWARDED_FOR')
        if x_forwarded_for:
            return x_forwarded_for.split(',')[0].strip()
        return request.META.get('REMOTE_ADDR')


@extend_schema(tags=['Auth'])
class LogoutView(APIView):
    """
    Logout user by blacklisting the refresh token.
    """
    permission_classes = [IsAuthenticated]

    def post(self, request):
        try:
            refresh_token = request.data.get('refresh')
            if refresh_token:
                token = RefreshToken(refresh_token)
                token.blacklist()

            # Set audit action
            request._audit_action = 'logout'

            return Response({
                'success': True,
                'message': 'Successfully logged out'
            }, status=status.HTTP_200_OK)

        except Exception as e:
            return Response({
                'success': False,
                'error': {
                    'message': 'Failed to logout',
                    'details': str(e)
                }
            }, status=status.HTTP_400_BAD_REQUEST)


@extend_schema(tags=['Auth'])
class TokenRefreshView(TokenRefreshView):
    """
    Refresh access token using refresh token.
    """
    throttle_classes = [AuthRateThrottle]


@extend_schema(tags=['Auth'])
class PasswordResetRequestView(APIView):
    """
    Request password reset email.
    """
    permission_classes = [AllowAny]
    throttle_classes = [PasswordResetThrottle]
    serializer_class = PasswordResetRequestSerializer

    def post(self, request):
        serializer = PasswordResetRequestSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)

        email = serializer.validated_data['email']

        # Always return success to prevent email enumeration
        # In production, send actual reset email here
        try:
            user = User.objects.get(email__iexact=email)
            # TODO: Send password reset email
            # send_password_reset_email.delay(user.id)

            request._audit_action = 'password_reset'
            request._audit_target_model = 'User'
            request._audit_target_id = str(user.id)
        except User.DoesNotExist:
            pass

        return Response({
            'success': True,
            'message': 'If an account with this email exists, a password reset link has been sent.'
        }, status=status.HTTP_200_OK)


@extend_schema(tags=['Auth'])
class PasswordResetConfirmView(APIView):
    """
    Confirm password reset with token.
    """
    permission_classes = [AllowAny]
    serializer_class = PasswordResetConfirmSerializer

    def post(self, request):
        serializer = PasswordResetConfirmSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)

        # TODO: Implement actual token validation
        # For now, return a placeholder response

        return Response({
            'success': True,
            'message': 'Password has been reset successfully'
        }, status=status.HTTP_200_OK)


@extend_schema(tags=['Auth'])
class ChangePasswordView(APIView):
    """
    Change password for authenticated user.
    """
    permission_classes = [IsAuthenticated]
    serializer_class = ChangePasswordSerializer

    def post(self, request):
        serializer = ChangePasswordSerializer(
            data=request.data,
            context={'request': request}
        )
        serializer.is_valid(raise_exception=True)
        serializer.save()

        # Set audit action
        request._audit_action = 'password_change'
        request._audit_target_model = 'User'
        request._audit_target_id = str(request.user.id)

        return Response({
            'success': True,
            'message': 'Password changed successfully'
        }, status=status.HTTP_200_OK)


# User Profile Views

@extend_schema(tags=['Users'])
class CurrentUserView(generics.RetrieveUpdateAPIView):
    """
    Get or update the current authenticated user's profile.
    """
    permission_classes = [IsAuthenticated]

    def get_serializer_class(self):
        if self.request.method in ['PUT', 'PATCH']:
            return UserUpdateSerializer
        return UserSerializer

    def get_object(self):
        return self.request.user

    def retrieve(self, request, *args, **kwargs):
        serializer = self.get_serializer(self.get_object())
        return Response({
            'success': True,
            'data': serializer.data
        })

    def update(self, request, *args, **kwargs):
        partial = kwargs.pop('partial', False)
        instance = self.get_object()
        serializer = self.get_serializer(instance, data=request.data, partial=partial)
        serializer.is_valid(raise_exception=True)
        self.perform_update(serializer)

        # Set audit action
        request._audit_action = 'user_update'
        request._audit_target_model = 'User'
        request._audit_target_id = str(request.user.id)

        return Response({
            'success': True,
            'message': 'Profile updated successfully',
            'data': UserSerializer(instance).data
        })


@extend_schema(tags=['Users'])
class DeleteAccountView(APIView):
    """
    Delete the current user's account.
    """
    permission_classes = [IsAuthenticated]

    def delete(self, request):
        user = request.user

        # Set audit action before deletion
        request._audit_action = 'user_delete'
        request._audit_target_model = 'User'
        request._audit_target_id = str(user.id)
        request._audit_description = f"User deleted account: {user.email}"

        # Soft delete or hard delete based on requirements
        user.is_active = False
        user.save()

        return Response({
            'success': True,
            'message': 'Account has been deactivated'
        }, status=status.HTTP_200_OK)
