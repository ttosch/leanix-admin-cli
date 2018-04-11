import json

from leanix_admin import graphql, file
from leanix_admin.action import BackupAction, RestoreAction

OTHER_TAGS = {'id': None, 'name': '__OTHER_TAGS__'}


def is_real_tag_group(tag_group):
    return tag_group['name'] != OTHER_TAGS['name']

def find_by_name(needle, haystack):
    for current in haystack:
        if needle['name'] == current['name']:
            return current
    return None


class TagGroupsBase:
    def __init__(self, http, graphql_url):
        self.http = http
        self.graphql_url = graphql_url

    def _fetch_tag_groups(self, erase_id=True):
        """
        Loads tag groups and tags via GraphQL and unwrap them from
        their edges/node structure. By default it strips the ids from the returned objects.
        This behavior can be changed by setting `erase_id=False`.
        """
        tag_group_lookup = {}
        by_name = lambda x: x['name']
        response = self._exec_graphql(graphql.list_tags)
        for tag_edge in response.get('listTags', {}).get('edges', []):
            tag = tag_edge['node']
            tag_group = tag['tagGroup'] or OTHER_TAGS.copy()

            if erase_id:
                del tag['id']
                del tag_group['id']

            del tag['tagGroup']

            group_name = tag_group['name']
            known_group = tag_group_lookup.get(group_name, {'tags': []})
            known_tags = known_group['tags']
            known_tags.append(tag)
            tag_group['tags'] = sorted(known_tags, key=by_name)
            tag_group_lookup[group_name] = tag_group

        return sorted(tag_group_lookup.values(), key=by_name)

    def _exec_graphql(self, query, variables=None):
        if variables is None:
            variables = {}
        body = {'operationName': None,
                'query': query,
                'variables': variables}
        r = self.http.post(self.graphql_url, json=body)
        r.raise_for_status()
        r_body = r.json()
        errors = r_body.get('errors', None)
        if errors:
            print(errors)
            print('Request: ', body)
            raise Exception()
        data = r_body.get('data', None)
        if not data:
            print('Request: ', body)
            raise Exception('Empty response data')
        return data


class TagGroupsBackupAction(TagGroupsBase, BackupAction):
    def __init__(self, http, graphql_url):
        TagGroupsBase.__init__(self, http, graphql_url)
        BackupAction.__init__(self, name='tag-groups')
        self.http = http
        self.graphql_url = graphql_url

    def do_perform(self):
        """
        Fetches tag groups including their tags and saves them to disk.
        """
        tag_groups = self._fetch_tag_groups()
        file.write_to_disk(self.name, tag_groups)


class TagGroupsRestoreAction(TagGroupsBase, RestoreAction):
    def __init__(self, http, graphql_url):
        TagGroupsBase.__init__(self, http, graphql_url)
        RestoreAction.__init__(self, name='tag-groups')
        self.http = http
        self.graphql_url = graphql_url

    def do_perform(self):
        """
        Loads the desired tag groups from disk and updates the remote ones accordingly:
        Obsolete groups are removed, new ones are added and existing ones are updated.
        """
        current_tag_groups = self._fetch_tag_groups(erase_id=False)
        desired_tag_groups = file.read_from_disk(self.name)

        for desired_tag_group in desired_tag_groups:
            current_tag_group = find_by_name(desired_tag_group, current_tag_groups)
            if current_tag_group:
                desired_tag_group['id'] = current_tag_group['id']
                if is_real_tag_group(desired_tag_group):
                    self._update_tag_group(desired_tag_group)
                current_tags = current_tag_group.get('tags', [])
            else:
                if is_real_tag_group(desired_tag_group):
                    self._create_tag_group(desired_tag_group)
                else:
                    desired_tag_group['id'] = None
                current_tags = []
            self._restore_tags(desired_tag_group['id'], desired_tag_group.get('tags', []), current_tags)

        for current_tag_group in current_tag_groups:
            if not find_by_name(current_tag_group, desired_tag_groups):
                for tag in current_tag_group.get('tags', []):
                    self._delete_tag(tag)
                if is_real_tag_group(current_tag_group):
                    self._delete_tag_group(current_tag_group)

    def _create_tag_group(self, tag_group):
        response = self._exec_graphql(graphql.create_tag_group, tag_group)
        tag_group['id'] = response['createTagGroup']['id']

    def _update_tag_group(self, tag_group):
        def tag_group_patches(tg):
            short_name = tg['shortName']
            description = tg['description']
            return [
                {'op': 'replace', 'path': '/mode', 'value': tg['mode']},
                {'op': 'replace', 'path': '/restrictToFactSheetTypes',
                 'value': json.dumps(tg['restrictToFactSheetTypes'])},
                ({'op': 'replace', 'path': '/shortName', 'value': short_name} if short_name else {'op': 'remove',
                                                                                                  'path': '/shortName'}),
                ({'op': 'replace', 'path': '/description', 'value': description} if description else {'op': 'remove',
                                                                                                      'path': '/description'})
            ]

        self._exec_graphql(graphql.update_tag_group, {'id': tag_group['id'], 'patches': tag_group_patches(tag_group)})

    def _delete_tag_group(self, tag_group):
        self._exec_graphql(graphql.delete_tag_group, {'id': tag_group['id']})

    def _restore_tags(self, tag_group_id, desired_tags, current_tags):
        for desired_tag in desired_tags:
            current_tag = find_by_name(desired_tag, current_tags)
            if current_tag:
                desired_tag['id'] = current_tag['id']
                self._update_tag(desired_tag)
            else:
                desired_tag['tagGroupId'] = tag_group_id
                self._create_tag(desired_tag)

        for current_tag in current_tags:
            if not find_by_name(current_tag, desired_tags):
                self._delete_tag(current_tag)

    def _create_tag(self, tag):
        response = self._exec_graphql(graphql.create_tag, tag)
        tag['id'] = response['createTag']['id']

    def _update_tag(self, tag):
        def tag_patches(t):
            description = t['description']
            return [
                ({'op': 'replace', 'path': '/description', 'value': description} if description else {'op': 'remove',
                                                                                                      'path': '/description'}),
                {'op': 'replace', 'path': '/color', 'value': t['color']},
                {'op': 'replace', 'path': '/status', 'value': t['status']}
            ]

        self._exec_graphql(graphql.update_tag, {'id': tag['id'], 'patches': tag_patches(tag)})

    def _delete_tag(self, tag):
        self._exec_graphql(graphql.delete_tag, {'id': tag['id']})
