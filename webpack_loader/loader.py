import json
import time
from io import open

from django.conf import settings
from django.contrib.staticfiles.storage import staticfiles_storage

from .exceptions import (
    WebpackError,
    WebpackLoaderBadStatsError,
    WebpackLoaderTimeoutError,
    WebpackBundleLookupError
)


class WebpackLoader(object):
    _assets = {}

    def __init__(self, name, config):
        self.name = name
        self.config = config

    def load_assets(self):
        try:
            with open(self.config['STATS_FILE'], encoding="utf-8") as f:
                return json.load(f)
        except IOError:
            raise IOError(
                'Error reading {0}. Are you sure webpack has generated '
                'the file and the path is correct?'.format(
                    self.config['STATS_FILE']))

    def get_assets(self):
        if self.config['CACHE']:
            if self.name not in self._assets:
                self._assets[self.name] = self.load_assets()
            return self._assets[self.name]
        return self.load_assets()

    def filter_chunks(self, chunks):
        for chunk in chunks:
            ignore = any(regex.match(chunk['name'])
                         for regex in self.config['ignores'])
            if not ignore:
                chunk['url'] = self.get_chunk_url(chunk)
                yield chunk

    def get_chunk_url(self, chunk):
        public_path = chunk.get('publicPath')
        if public_path:
            return public_path

        relpath = '{0}{1}'.format(
            self.config['BUNDLE_DIR_NAME'], chunk['name']
        )
        return staticfiles_storage.url(relpath)

    def get_bundle(self, bundle_name):
        assets = self.get_assets()

        # poll when debugging and block request until bundle is compiled
        # or the build times out
        if settings.DEBUG:
            assets = self.poll_while_compiling(assets, bundle_name)

        if assets.get('status') == 'done':
            chunks = assets['chunks'].get(bundle_name, None)
            if chunks is None:
                raise WebpackBundleLookupError('Cannot resolve bundle {0}.'.format(bundle_name))
            return self.filter_chunks(chunks)

        elif assets.get('status') == 'error':
            if 'file' not in assets:
                assets['file'] = ''
            if 'error' not in assets:
                assets['error'] = 'Unknown Error'
            if 'message' not in assets:
                assets['message'] = ''
            error = u"""
            {error} in {file}
            {message}
            """.format(**assets)
            raise WebpackError(error)

        raise WebpackLoaderBadStatsError(
            "The stats file does not contain valid data. Make sure "
            "webpack-bundle-tracker plugin is enabled and try to run "
            "webpack again.")

    def get_entry(self, entry_name):
        assets = self.get_assets()

        # poll when debugging and block request until bundle is compiled
        # or the build times out
        if settings.DEBUG:
            assets = self.poll_while_compiling(assets, entry_name)

        if assets.get('status') == 'done':
            if 'entryPoints' in assets:
                entry_files = assets['entryPoints'].get(entry_name, None)
                if entry_files:
                    entry_files_flat = [entry_point for sublist in entry_files for entry_point in sublist]
                    if 'runtime' in entry_files_flat[0]['name'] and self.config['EXCLUDE_RUNTIME']:
                        entry_files_flat.pop(0)
                    if self.config['BASE_ENTRYPOINT'] and entry_name != self.config['BASE_ENTRYPOINT']:
                        base_entrypoint = self.config['BASE_ENTRYPOINT']
                        base_entry_files = assets['entryPoints'].get(base_entrypoint, None)
                        if base_entry_files:
                            base_entry_files_flat_names = [entry_point['name'] for sublist in base_entry_files for entry_point in sublist]
                            for i, entry in enumerate(entry_files_flat):
                                if entry['name'] in base_entry_files_flat_names:
                                    entry_files_flat.pop(i)
                else:
                    raise WebpackBundleLookupError('Cannot resolve entry {0}.'.format(entry_name))
            else:
                raise WebpackBundleLookupError(
                    'No entrypoints were found in the stats file. Make sure you '
                    'are using a supported version of webpack and double check '
                    'your webpack configuration.')

            return self.filter_chunks(entry_files_flat)
        elif assets.get('status') == 'error':
            if 'file' not in assets:
                assets['file'] = ''
            if 'error' not in assets:
                assets['error'] = 'Unknown Error'
            if 'message' not in assets:
                assets['message'] = ''
            error = u"""
            {error} in {file}
            {message}
            """.format(**assets)
            raise WebpackError(error)

        raise WebpackLoaderBadStatsError(
            "The stats file does not contain valid data. Make sure "
            "webpack-bundle-tracker plugin is enabled and try to run "
            "webpack again.")

    def poll_while_compiling(self, assets, entry_name):
        timeout = self.config['TIMEOUT'] or 0
        start = time.time()
        while assets['status'] == 'compiling':
            time.sleep(self.config['POLL_INTERVAL'])
            if timeout and (time.time() - timeout > start):
                raise WebpackLoaderTimeoutError(
                    "Timed Out. Bundle `{0}` took more than {1} seconds "
                    "to compile.".format(entry_name, timeout)
                )
            assets = self.get_assets()

        return assets
