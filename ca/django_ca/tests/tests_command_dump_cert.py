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

"""Test the dump_cert management command."""

import os
from io import BytesIO

from cryptography.hazmat.primitives.serialization import Encoding

from .. import ca_settings
from .base import DjangoCAWithGeneratedCertsTestCase
from .base import override_tmpcadir


class DumpCertTestCase(DjangoCAWithGeneratedCertsTestCase):
    """Main test class for this command."""

    def setUp(self):
        super().setUp()
        self.cert = self.certs["root-cert"]

    def test_basic(self):
        """Basic test of this command."""
        stdout, stderr = self.cmd("dump_cert", self.cert.serial, stdout=BytesIO(), stderr=BytesIO())
        self.assertEqual(stderr, b"")
        self.assertEqual(stdout, self.cert.pub.encode("utf-8"))

    def test_format(self):
        """Test various formats."""
        for option in ["PEM", "DER"]:
            encoding = getattr(Encoding, option)
            stdout, stderr = self.cmd(
                "dump_cert", self.cert.serial, format=encoding, stdout=BytesIO(), stderr=BytesIO()
            )
            self.assertEqual(stderr, b"")
            self.assertEqual(stdout, self.cert.dump_certificate(encoding))

    def test_explicit_stdout(self):
        """Test writing to stdout."""
        stdout, stderr = self.cmd("dump_cert", self.cert.serial, "-", stdout=BytesIO(), stderr=BytesIO())
        self.assertEqual(stderr, b"")
        self.assertEqual(stdout, self.cert.pub.encode("utf-8"))

    def test_bundle(self):
        """Test getting the bundle."""
        stdout, stderr = self.cmd(
            "dump_cert", self.cert.serial, bundle=True, stdout=BytesIO(), stderr=BytesIO()
        )
        self.assertEqual(stderr, b"")
        self.assertEqual(stdout, self.cert.pub.encode("utf-8") + self.cas["root"].pub.encode("utf-8"))

    @override_tmpcadir()
    def test_file_output(self):
        """Test writing to a file."""
        path = os.path.join(ca_settings.CA_DIR, "test_cert.pem")
        stdout, stderr = self.cmd("dump_cert", self.cert.serial, path, stdout=BytesIO(), stderr=BytesIO())
        self.assertEqual(stderr, b"")
        self.assertEqual(stdout, b"")

        with open(path) as stream:
            self.assertEqual(stream.read(), self.cert.pub)

    def test_errors(self):
        """Test some error conditions."""
        path = os.path.join(ca_settings.CA_DIR, "does-not-exist", "test_cert.pem")
        msg = r"^\[Errno 2\] No such file or directory: '%s/does-not-exist/test_cert\.pem'$" % (
            ca_settings.CA_DIR
        )
        with self.assertCommandError(msg):
            self.cmd("dump_cert", self.cert.serial, path, stdout=BytesIO(), stderr=BytesIO())

        with self.assertCommandError(r"^Cannot dump bundle when using DER format\.$"):
            self.cmd(
                "dump_cert",
                self.cert.serial,
                format=Encoding.DER,
                bundle=True,
                stdout=BytesIO(),
                stderr=BytesIO(),
            )
