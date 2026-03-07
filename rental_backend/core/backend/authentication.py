from rest_framework import exceptions
from rest_framework.authentication import BaseAuthentication

from backend.models import Token, VendorToken


class TokenAuthentication(BaseAuthentication):
    """Accept both 'Token <key>' and 'Bearer <key>' so app can use Bearer. Invalid token = anonymous."""

    def authenticate(self, request):
        token_header = request.headers.get('Authorization')
        if not token_header:
            return None, None
        parts = str(token_header).strip().split()
        if len(parts) != 2 or not parts[1]:
            return None, None
        # Accept "Token" or "Bearer" (Flutter app sends Bearer)
        if parts[0].lower() not in ('token', 'bearer'):
            return None, None
        token_value = parts[1].strip()
        if not token_value:
            return None, None
        try:
            user = Token.objects.get(token=token_value).user
            return user, None
        except (Token.DoesNotExist, IndexError, ValueError):
            return None, None


class VendorTokenAuthentication(BaseAuthentication):
    def authenticate(self, request):
        token = request.headers.get('Authorization')
        if not token:
            return None, None

        try:
            token_parts = str(token).split()
            if len(token_parts) != 2 or token_parts[0].lower() not in ['token', 'Token']:
                raise exceptions.AuthenticationFailed('Invalid token format')

            token_value = token_parts[1]

            # Get vendor token (no expiration check - stays valid until logout)
            vendor_token = VendorToken.objects.select_related('vendor').get(token=token_value)

            # Check if vendor is active
            if not vendor_token.vendor.is_active:
                raise exceptions.AuthenticationFailed('Vendor account is deactivated')

            return vendor_token.vendor, None

        except VendorToken.DoesNotExist:
            raise exceptions.AuthenticationFailed('Invalid or expired vendor token')
        except Exception as e:
            raise exceptions.AuthenticationFailed(f'Authentication failed: {str(e)}')