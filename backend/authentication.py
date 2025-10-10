from rest_framework import exceptions
from rest_framework.authentication import BaseAuthentication

from backend.models import Token, VendorToken


class TokenAuthentication(BaseAuthentication):

    def authenticate(self, request):
        token = request.headers.get('Authorization')
        print(str(token).split())
        if token:
            try:
                user = Token.objects.get(token=str(token).split()[1]).user
            except:
                raise exceptions.AuthenticationFailed('unauthenticated_user')
        else:
            user = None

        return user, None


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

            vendor_token = VendorToken.objects.select_related('vendor').get(token=token_value)
            if not vendor_token.vendor.is_active:
                raise exceptions.AuthenticationFailed('Vendor account is deactivated')

            return vendor_token.vendor, None

        except VendorToken.DoesNotExist:
            raise exceptions.AuthenticationFailed('Invalid or expired vendor token')
        except Exception as e:
            raise exceptions.AuthenticationFailed(f'Authentication failed: {str(e)}')
