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

import csv
import io
from collections import OrderedDict
from operator import itemgetter
from contextlib import closing

from django.db import connection
from django.utils import timezone
from django.utils.translation import ugettext as _

from taiga.base.utils import db, text
from taiga.projects.history.services import take_snapshot
from taiga.projects.userstories.apps import (
    connect_userstories_signals,
    disconnect_userstories_signals)

from taiga.events import events
from taiga.projects.votes import services as votes_services

from . import models


def get_userstories_from_bulk(bulk_data, **additional_fields):
    """Convert `bulk_data` into a list of user stories.

    :param bulk_data: List of user stories in bulk format.
    :param additional_fields: Additional fields when instantiating each user story.

    :return: List of `UserStory` instances.
    """
    return [models.UserStory(subject=line, **additional_fields)
            for line in text.split_in_lines(bulk_data)]


def create_userstories_in_bulk(bulk_data, callback=None, precall=None, **additional_fields):
    """Create user stories from `bulk_data`.

    :param bulk_data: List of user stories in bulk format.
    :param callback: Callback to execute after each user story save.
    :param additional_fields: Additional fields when instantiating each user story.

    :return: List of created `Task` instances.
    """
    userstories = get_userstories_from_bulk(bulk_data, **additional_fields)

    disconnect_userstories_signals()

    try:
        db.save_in_bulk(userstories, callback, precall)
    finally:
        connect_userstories_signals()

    return userstories


def update_userstories_order_in_bulk(bulk_data:list, field:str, project:object):
    """
    Update the order of some user stories.
    `bulk_data` should be a list of tuples with the following format:

    [(<user story id>, {<field>: <value>, ...}), ...]
    """
    user_story_ids = []
    new_order_values = []
    for us_data in bulk_data:
        user_story_ids.append(us_data["us_id"])
        new_order_values.append({field: us_data["order"]})

    events.emit_event_for_ids(ids=user_story_ids,
                              content_type="userstories.userstory",
                              projectid=project.pk)

    db.update_in_bulk_with_ids(user_story_ids, new_order_values, model=models.UserStory)


def snapshot_userstories_in_bulk(bulk_data, user):
    user_story_ids = []
    for us_data in bulk_data:
        try:
            us = models.UserStory.objects.get(pk=us_data['us_id'])
            take_snapshot(us, user=user)
        except models.UserStory.DoesNotExist:
            pass


def calculate_userstory_is_closed(user_story):
    if user_story.status is None:
        return False

    if user_story.tasks.count() == 0:
        return user_story.status.is_closed

    if all([task.status.is_closed for task in user_story.tasks.all()]):
        return True

    return False


def close_userstory(us):
    if not us.is_closed:
        us.is_closed = True
        us.finish_date = timezone.now()
        us.save(update_fields=["is_closed", "finish_date"])


def open_userstory(us):
    if us.is_closed:
        us.is_closed = False
        us.finish_date = None
        us.save(update_fields=["is_closed", "finish_date"])


def userstories_to_csv(project,queryset):
    csv_data = io.StringIO()
    fieldnames = ["ref", "subject", "description", "milestone", "owner",
                  "owner_full_name", "assigned_to", "assigned_to_full_name",
                  "status", "is_closed"]
    for role in project.roles.filter(computable=True).order_by('name'):
        fieldnames.append("{}-points".format(role.slug))
    fieldnames.append("total-points")

    fieldnames += ["backlog_order", "sprint_order", "kanban_order",
                   "created_date", "modified_date", "finish_date",
                   "client_requirement", "team_requirement", "attachments",
                   "generated_from_issue", "external_reference", "tasks",
                   "tags",
                   "watchers", "voters"]

    for custom_attr in project.userstorycustomattributes.all():
        fieldnames.append(custom_attr.name)

    writer = csv.DictWriter(csv_data, fieldnames=fieldnames)
    writer.writeheader()
    for us in queryset:
        row = {
            "ref": us.ref,
            "subject": us.subject,
            "description": us.description,
            "milestone": us.milestone.name if us.milestone else None,
            "owner": us.owner.username,
            "owner_full_name": us.owner.get_full_name(),
            "assigned_to": us.assigned_to.username if us.assigned_to else None,
            "assigned_to_full_name": us.assigned_to.get_full_name() if us.assigned_to else None,
            "status": us.status.name,
            "is_closed": us.is_closed,
            "backlog_order": us.backlog_order,
            "sprint_order": us.sprint_order,
            "kanban_order": us.kanban_order,
            "created_date": us.created_date,
            "modified_date": us.modified_date,
            "finish_date": us.finish_date,
            "client_requirement": us.client_requirement,
            "team_requirement": us.team_requirement,
            "attachments": us.attachments.count(),
            "generated_from_issue": us.generated_from_issue.ref if us.generated_from_issue else None,
            "external_reference": us.external_reference,
            "tasks": ",".join([str(task.ref) for task in us.tasks.all()]),
            "tags": ",".join(us.tags or []),
            "watchers": [u.id for u in us.get_watchers()],
            "voters": votes_services.get_voters(us).count(),
        }

        for role in us.project.roles.filter(computable=True).order_by('name'):
            if us.role_points.filter(role_id=role.id).count() == 1:
                row["{}-points".format(role.slug)] = us.role_points.get(role_id=role.id).points.value
            else:
                row["{}-points".format(role.slug)] = 0
        row['total-points'] = us.get_total_points()

        for custom_attr in project.userstorycustomattributes.all():
            value = us.custom_attributes_values.attributes_values.get(str(custom_attr.id), None)
            row[custom_attr.name] = value

        writer.writerow(row)

    return csv_data


def _get_userstories_statuses(project, queryset):
    compiler = connection.ops.compiler(queryset.query.compiler)(queryset.query, connection, None)
    queryset_where_tuple = queryset.query.where.as_sql(compiler, connection)
    where = queryset_where_tuple[0]
    where_params = queryset_where_tuple[1]

    extra_sql = """
      SELECT "projects_userstorystatus"."id",
             "projects_userstorystatus"."name",
             "projects_userstorystatus"."color",
             "projects_userstorystatus"."order",
             (SELECT count(*)
                FROM "userstories_userstory"
                     INNER JOIN "projects_project" ON
                                ("userstories_userstory"."project_id" = "projects_project"."id")
               WHERE {where} AND "userstories_userstory"."status_id" = "projects_userstorystatus"."id")
        FROM "projects_userstorystatus"
       WHERE "projects_userstorystatus"."project_id" = %s
    ORDER BY "projects_userstorystatus"."order";
    """.format(where=where)

    with closing(connection.cursor()) as cursor:
        cursor.execute(extra_sql, where_params + [project.id])
        rows = cursor.fetchall()

    result = []
    for id, name, color, order, count in rows:
        print(name) 
        result.append({
            "id": id,
            "name": _(name),
            "color": color,
            "order": order,
            "count": count,
        })
    return sorted(result, key=itemgetter("order"))


def _get_userstories_assigned_to(project, queryset):
    compiler = connection.ops.compiler(queryset.query.compiler)(queryset.query, connection, None)
    queryset_where_tuple = queryset.query.where.as_sql(compiler, connection)
    where = queryset_where_tuple[0]
    where_params = queryset_where_tuple[1]

    extra_sql = """
          SELECT NULL,
                 NULL,
                 (SELECT count(*)
                    FROM "userstories_userstory"
                         INNER JOIN "projects_project" ON
                                    ("userstories_userstory"."project_id" = "projects_project"."id" )
                   WHERE {where} AND "userstories_userstory"."assigned_to_id" IS NULL)
    UNION SELECT "users_user"."id",
                 "users_user"."full_name",
                 (SELECT count(*)
                    FROM "userstories_userstory"
                         INNER JOIN "projects_project" ON
                                    ("userstories_userstory"."project_id" = "projects_project"."id" )
                   WHERE {where} AND "userstories_userstory"."assigned_to_id" = "projects_membership"."user_id")
            FROM "projects_membership"
                 INNER JOIN "users_user" ON
                            ("projects_membership"."user_id" = "users_user"."id")
           WHERE "projects_membership"."project_id" = %s AND "projects_membership"."user_id" IS NOT NULL;
    """.format(where=where)

    with closing(connection.cursor()) as cursor:
        cursor.execute(extra_sql, where_params + where_params + [project.id])
        rows = cursor.fetchall()

    result = []
    for id, full_name, count in rows:
        result.append({
            "id": id,
            "full_name": full_name or "",
            "count": count,
        })
    return sorted(result, key=itemgetter("full_name"))


def _get_userstories_owners(project, queryset):
    compiler = connection.ops.compiler(queryset.query.compiler)(queryset.query, connection, None)
    queryset_where_tuple = queryset.query.where.as_sql(compiler, connection)
    where = queryset_where_tuple[0]
    where_params = queryset_where_tuple[1]

    extra_sql = """
       SELECT "users_user"."id",
              "users_user"."full_name",
              (SELECT count(*)
                 FROM "userstories_userstory"
                      INNER JOIN "projects_project" ON
                                 ("userstories_userstory"."project_id" = "projects_project"."id")
                WHERE {where} AND "userstories_userstory"."owner_id" = "projects_membership"."user_id")
        FROM "projects_membership"
             RIGHT OUTER JOIN "users_user" ON
                              ("projects_membership"."user_id" = "users_user"."id")
       WHERE (("projects_membership"."project_id" = %s AND "projects_membership"."user_id" IS NOT NULL)
              OR ("users_user"."is_system" IS TRUE));
    """.format(where=where)

    with closing(connection.cursor()) as cursor:
        cursor.execute(extra_sql, where_params + [project.id])
        rows = cursor.fetchall()

    result = []
    for id, full_name, count in rows:
        if count > 0:
            result.append({
                "id": id,
                "full_name": full_name,
                "count": count,
            })
    return sorted(result, key=itemgetter("full_name"))


def _get_userstories_tags(queryset):
    
    tags = []
    for t_list in queryset.values_list("tags", flat=True):
        print(t_list) 
        if t_list is None:
            continue
        tags += list(t_list)

    tags = [{"name":e, "count":tags.count(e)} for e in set(tags)]

    return sorted(tags, key=itemgetter("name"))


def _get_userstories_client_requirements(project, queryset):
     #return True
     # import logging
     # logger = logging.getLogger(__name__)
     # # logger.info('=================================' + queryset.values_list('client_requirement'))
     # # logger.info('=================================' + queryset.values_list('client_requirement'))
    #client_requirement = []
     #for c_list in queryset.values_list("client_requirement", flat=True):
     #     logger.info('____________________ c_list: ' + str(c_list))
    #client_requirement += queryset.values_list("client_requirement", flat=True)
    compiler = connection.ops.compiler(queryset.query.compiler)(queryset.query, connection, None)
    queryset_where_tuple = queryset.query.where.as_sql(compiler, connection)
    where = queryset_where_tuple[0]
    where_params = queryset_where_tuple[1]
    
    extra_sql = """
      SELECT "project_id",
              "client_requirement"
              FROM "userstories_userstory" 
              INNER JOIN "projects_project" ON
                                ("userstories_userstory"."project_id" = "projects_project"."id")
        
       WHERE ("client_requirement" IS TRUE AND "is_closed" IS FALSE);
    """.format(where=where)

    with closing(connection.cursor()) as cursor:
        cursor.execute(extra_sql, where_params + [project.id])
        rows = cursor.fetchall()

    result = []
    for project_id, client_requirement in rows:
        if project_id == project.id:
            result.append({
            "project_id": project_id,
            "client_requirement": client_requirement,
            })
    return sorted(result, key=itemgetter("project_id"))
          #if c_list is None: # and us.client_requirement is True: # !!!
              #continue                       
          #client_requirements += c_list 
     #print(client_requirement)
     #return client_requirement 


def _get_userstories_team_requirements(project, queryset):
    #team_requirement = []
    #team_requirement += queryset.values_list("team_requirement", flat=True)
    compiler = connection.ops.compiler(queryset.query.compiler)(queryset.query, connection, None)
    queryset_where_tuple = queryset.query.where.as_sql(compiler, connection)
    where = queryset_where_tuple[0]
    where_params = queryset_where_tuple[1]

    extra_sql = """
      SELECT "project_id",
              "team_requirement"
              FROM "userstories_userstory" 
              INNER JOIN "projects_project" ON
                                ("userstories_userstory"."project_id" = "projects_project"."id")
        
       WHERE ("team_requirement" IS TRUE AND "is_closed" IS FALSE);
    """.format(where=where)

    with closing(connection.cursor()) as cursor:
        cursor.execute(extra_sql, where_params + [project.id])
        rows = cursor.fetchall()

    result = []
    for project_id, team_requirement in rows:
        if project_id == project.id:
            result.append({
            "project_id": project_id,
            "team_requirement": team_requirement,
            })
    return sorted(result, key=itemgetter("project_id"))
    #print(team_requirement)
    #return team_requirement

def get_userstories_filters_data(project, querysets):
    """
    Given a project and an userstories queryset, return a simple data structure
    of all possible filters for the userstories in the queryset.
    """
    print(project)
    data = OrderedDict([
        ("statuses", _get_userstories_statuses(project, querysets["statuses"])),
        ("assigned_to", _get_userstories_assigned_to(project, querysets["assigned_to"])),
        ("owners", _get_userstories_owners(project, querysets["owners"])),
        ("tags", _get_userstories_tags(querysets["tags"])),
        ("client_requirement", _get_userstories_client_requirements(project, querysets["client_requirement"])),
        ("team_requirement", _get_userstories_team_requirements(project, querysets["team_requirement"])),
    ])
    print(data)
    return data
