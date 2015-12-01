# Copyright (C) 2014-2015 Andrey Antukh <niwi@niwi.be>
# Copyright (C) 2014-2015 Jesús Espino <jespinog@gmail.com>
# Copyright (C) 2014-2015 David Barragán <bameda@dbarragan.com>
# This program is free software: you can redistribute it and/or modify
# it under the terms of the GNU Affero General Public License as
# published by the Free Software Foundation, either version 3 of the
# License, or (at your option) any later version.
#
# This program is distributed in the hope that it will be useful,
# but WITHOUT ANY WARRANTY; without even the implied warranty of
# MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the
# GNU Affero General Public License for more details.
#
# You should have received a copy of the GNU Affero General Public License
# along with this program.  If not, see <http://www.gnu.org/licenses/>.

from django.apps import apps
from django.forms import widgets

from taiga.base.api import serializers
from taiga.base.fields import JsonField
from taiga.users.services import get_photo_or_gravatar_url, get_big_photo_or_gravatar_url

from . import models
from . import service


class TimelineSerializer(serializers.ModelSerializer):
    data = serializers.SerializerMethodField("get_data")

    class Meta:
        model = models.Timeline

    def get_data(self, obj):
        #Updates the data user info saved if the user exists
        if hasattr(obj, "_prefetched_user"):
            user = obj._prefetched_user
        else:
            User = apps.get_model("users", "User")
            userData = obj.get("user", None)
            try:
                user = User.objects.get(id=userData["id"])
            except User.DoesNotExist:
                user = None

        if user is not None:
            obj.data["user"] = {
                "id": user.pk,
                "name": user.get_full_name(),
                "photo": get_photo_or_gravatar_url(user),
                "big_photo": get_big_photo_or_gravatar_url(user),
                "username": user.username,
                "is_profile_visible": user.is_active and not user.is_system,
                "date_joined": user.date_joined
            }

        return obj.data
