# Copyright (C) 2014-2015 Andrey Antukh <niwi@niwi.be>
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

from taiga.projects.history import services as history_service

def on_new_history_entry(sender, instance, created, **kwargs):
    model = history_service.get_model_from_key(instance.key)
    pk = history_service.get_pk_from_key(instance.key)
    obj = model.objects.get(pk=pk)
    project = obj.project
    project.refresh_totals()
