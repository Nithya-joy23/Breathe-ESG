import base64
import hashlib
import hmac
import json
import time

from django.conf import settings
from django.contrib.auth.models import User
from rest_framework.authentication import BaseAuthentication, SessionAuthentication
from rest_framework.exceptions import AuthenticationFailed


def _b64url_encode(value):
    return base64.urlsafe_b64encode(value).rstrip(b'=').decode('ascii')


def _b64url_decode(value):
    padding = '=' * (-len(value) % 4)
    return base64.urlsafe_b64decode((value + padding).encode('ascii'))


def create_access_token(user):
    now = int(time.time())
    payload = {
        'sub': user.id,
        'username': user.username,
        'iat': now,
        'exp': now + getattr(settings, 'ACCESS_TOKEN_MAX_AGE_SECONDS', 60 * 60 * 12),
    }
    header = {'alg': 'HS256', 'typ': 'JWT'}
    signing_input = '.'.join([
        _b64url_encode(json.dumps(header, separators=(',', ':')).encode('utf-8')),
        _b64url_encode(json.dumps(payload, separators=(',', ':')).encode('utf-8')),
    ])
    signature = hmac.new(
        settings.SECRET_KEY.encode('utf-8'),
        signing_input.encode('ascii'),
        hashlib.sha256,
    ).digest()
    return f'{signing_input}.{_b64url_encode(signature)}'


class BearerTokenAuthentication(BaseAuthentication):
    keyword = 'Bearer'

    def authenticate(self, request):
        auth_header = request.headers.get('Authorization', '')
        if not auth_header:
            return None

        try:
            keyword, token = auth_header.split(' ', 1)
        except ValueError:
            raise AuthenticationFailed('Invalid authorization header')

        if keyword != self.keyword:
            return None

        try:
            header_part, payload_part, signature_part = token.split('.')
            signing_input = f'{header_part}.{payload_part}'
            expected_signature = hmac.new(
                settings.SECRET_KEY.encode('utf-8'),
                signing_input.encode('ascii'),
                hashlib.sha256,
            ).digest()
            received_signature = _b64url_decode(signature_part)
            if not hmac.compare_digest(expected_signature, received_signature):
                raise AuthenticationFailed('Invalid token')

            payload = json.loads(_b64url_decode(payload_part).decode('utf-8'))
            if int(payload.get('exp', 0)) < int(time.time()):
                raise AuthenticationFailed('Token expired')

            user = User.objects.get(id=payload.get('sub'), is_active=True)
        except (ValueError, json.JSONDecodeError, User.DoesNotExist):
            raise AuthenticationFailed('Invalid token')

        return (user, token)

    def authenticate_header(self, request):
        return self.keyword


class CsrfExemptSessionAuthentication(SessionAuthentication):
    def enforce_csrf(self, request):
        return None
