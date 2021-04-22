# This file is part of django-ca (https://github.com/mathiasertl/django-ca).
#
# django-ca is free software: you can redistribute it and/or modify it under the terms of the GNU
# General Public License as published by the Free Software Foundation, either version 3 of the
# License, or (at your option) any later version.
#
# django-ca is distributed in the hope that it will be useful, but WITHOUT ANY WARRANTY; without
# even the implied warranty of MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the GNU
# General Public License for more details.
#
# You should have received a copy of the GNU General Public License along with django-ca.  If not,
# see <http://www.gnu.org/licenses/>

"""Test the list_certs management command."""

import typing

from django.test import TestCase
from django.utils import timezone

from freezegun import freeze_time

from ..models import Certificate
from ..utils import add_colons
from .base import override_settings
from .base import timestamps
from .base.mixins import TestCaseMixin


class ListCertsTestCase(TestCaseMixin, TestCase):
    """Main test class for this command."""

    load_cas = "__usable__"
    load_certs = "__usable__"

    def _line(self, cert: Certificate) -> str:
        if cert.revoked is True:
            info = "revoked"
        else:
            word = "expires"
            if cert.expires < timezone.now():
                word = "expired"

            info = "%s: %s" % (word, cert.expires.strftime("%Y-%m-%d"))
        return "%s - %s (%s)" % (add_colons(cert.serial), cert.cn, info)

    def assertCerts(self, *certs: Certificate, **kwargs: typing.Any) -> None:  # pylint: disable=invalid-name
        """Assert that command outputs the given certs."""
        stdout, stderr = self.cmd("list_certs", **kwargs)
        sorted_certs = sorted(certs, key=lambda c: (c.expires, c.cn, c.serial))
        self.assertEqual(stdout, "".join(["%s\n" % self._line(c) for c in sorted_certs]))
        self.assertEqual(stderr, "")

    @freeze_time(timestamps["everything_valid"])
    def test_basic(self) -> None:
        """Basic test."""
        self.assertCerts(*self.new_certs.values())

    @freeze_time(timestamps["everything_expired"])
    def test_expired(self) -> None:
        """Test listing of expired certs."""
        self.assertCerts()
        self.assertCerts(*self.new_certs.values(), expired=True)

    @freeze_time(timestamps["everything_valid"])
    def test_revoked(self) -> None:
        """Test listing of revoked certs."""
        cert = self.new_certs["root-cert"]
        cert.revoke()

        self.assertCerts(*[c for c in self.new_certs.values() if c != cert])
        self.assertCerts(*self.new_certs.values(), revoked=True)

    @freeze_time(timestamps["everything_valid"])
    def test_autogenerated(self) -> None:
        """Test listing of autogenerated certs."""
        cert = self.new_certs["root-cert"]
        cert.autogenerated = True
        cert.save()

        self.assertCerts(*[c for c in self.new_certs.values() if c != cert])
        self.assertCerts(*self.new_certs.values(), autogenerated=True)

    @freeze_time(timestamps["everything_valid"])
    def test_ca(self) -> None:
        """Test listing for all CAs."""
        for ca in self.new_cas.values():
            self.assertCerts(*[c for c in self.new_certs.values() if c.ca == ca], ca=ca)


@override_settings(USE_TZ=True)
class ListCertsWithTZTestCase(ListCertsTestCase):
    """Tests but with timezone support."""
