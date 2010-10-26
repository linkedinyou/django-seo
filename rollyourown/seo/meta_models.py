#!/usr/bin/env python
# -*- coding: UTF-8 -*-

from django.utils.translation import ugettext_lazy as _
from django.conf import settings
from django.db import models
from django.contrib.sites.models import Site
from django.contrib.contenttypes.models import ContentType
from django.contrib.contenttypes import generic
from django.template import Template, Context

from rollyourown.seo.systemviews import SystemViewField
from rollyourown.seo.utils import resolve_to_name, NotSet, Literal

RESERVED_FIELD_NAMES = ('_metadata', '_path', '_content_type', '_object_id',
                        '_content_object', '_view', '_site', 'objects', 
                        '_resolve_value', '_set_context', 'id', 'pk' )


class MetadataBaseModel(models.Model):

    class Meta:
        abstract = True

    def __init__(self, *args, **kwargs):
        super(MetadataBaseModel, self).__init__(*args, **kwargs)

        # Provide access to a class instance
        # TODO Rename to __metadata
        self._metadata = self.__class__._metadata()

    # TODO Rename to __resolve_value
    def _resolve_value(self, name):
        """ Returns an appropriate value for the given name. """
        name = str(name)
        if name in self._metadata.elements:
            element = self._metadata.elements[name]

            # Look in instances for an explicit value
            if element.editable:
                value = getattr(self, name)
                if value:
                    return value

            # Otherwise, return an appropriate default value (populate_from)
            populate_from = element.populate_from
            if callable(populate_from):
                if getattr(populate_from, 'im_self', None):
                    return populate_from()
                else:
                    return populate_from(self._metadata)
            elif isinstance(populate_from, Literal):
                return populate_from.value
            elif populate_from is not NotSet:
                return self._resolve_value(populate_from)

        # If this is not an element, look for an attribute on metadata
        try:
            value = getattr(self._metadata, name)
        except AttributeError:
            pass
        else:
            if callable(value):
                if getattr(value, 'im_self', None):
                    return value()
                else:
                    return value(self._metadata)
            return value


class BaseManager(models.Manager):
    def on_current_site(self, site=None):
        if isinstance(site, Site):
            site_id = site.id
        elif site is not None:
            site_id = site and Site.objects.get(domain=site).id
        else:
            site_id = settings.SITE_ID
        # Exclude entries for other sites
        where = ['_site_id IS NULL OR _site_id=%s']
        return self.get_query_set().extra(where=where, params=[site_id])

    def for_site_and_language(self, site=None, language=None):
        queryset = self.on_current_site(site)
        if language:
            queryset = queryset.filter(_language=language)
        return queryset


class MetadataPlugin(object):
    name = None
    unique_together = None

    def get_unique_together(self, options):
        ut = []
        for ut_set in self.unique_together:
            ut_set = [a for a in ut_set]
            if options.use_sites:
                ut_set.append('_site')
            if options.use_i18n:
                ut_set.append('_language')
            ut.append(tuple(ut_set))
        return tuple(ut)

    def get_manager(self, options):
        _get_from_path = self.get_from_path

        class _Manager(BaseManager):
            def get_from_path(self, path, site=None, language=None):
                queryset = self.for_site_and_language(site, language)
                return _get_from_path(queryset, path)

            if not options.use_sites:
                def for_site_and_language(self, site=None, language=None):
                    queryset = self.get_query_set()
                    if language:
                        queryset = queryset.filter(_language=language)
                    return queryset
        return _Manager


class PathMetadataPlugin(MetadataPlugin):

    unique_together = (("_path",),)

    def get_from_path(self, queryset, path):
        return queryset.get(_path=path)

    def get_model(self, options):
        class PathMetadataBase(MetadataBaseModel):
            _path = models.CharField(_('path'), max_length=511, unique=not (options.use_sites or options.use_i18n))
            if options.use_sites:
                _site = models.ForeignKey(Site, null=True, blank=True)
            if options.use_i18n:
                _language = models.CharField(max_length=5, null=True, blank=True, db_index=True)
            objects = self.get_manager(options)()

            def __unicode__(self):
                return self._path
    
            class Meta:
                abstract = True
                unique_together = self.get_unique_together(options)

        return PathMetadataBase


class ViewMetadataPlugin(MetadataPlugin):

    unique_together = (("_view",),)

    def get_from_path(self, queryset, path):
        view_name = resolve_to_name(path)
        if view_name is not None:
            return queryset.get(_view=view_name)
        raise queryset.model.DoesNotExist()

    def get_model(self, options):
        class ViewMetadataBase(MetadataBaseModel):
            _view = SystemViewField(unique=not (options.use_sites or options.use_i18n))
            if options.use_sites:
                _site = models.ForeignKey(Site, null=True, blank=True)
            if options.use_i18n:
                _language = models.CharField(max_length=5, null=True, blank=True, db_index=True)
            objects = self.get_manager(options)()

            def _set_context(self, context):
                """ Use the context when rendering any substitutions.  """
                self.__context = context
        
            def _resolve_value(self, name):
                value = super(ViewMetadataBase, self)._resolve_value(name)
                return _resolve(value, context=self.__context)

            def __unicode__(self):
                return self._view
    
            class Meta:
                abstract = True
                unique_together = self.get_unique_together(options)

        return ViewMetadataBase


class ModelInstanceMetadataPlugin(MetadataPlugin):

    name = "modelinstance"
    unique_together = (("_path",), ("_content_type", "_object_id"))

    def get_from_path(self, queryset, path):
        return queryset.get(_path=path)

    def get_model(self, options):
        class ModelInstanceMetadataBase(MetadataBaseModel):
            _path = models.CharField(_('path'), max_length=511, unique=not (options.use_sites or options.use_i18n))
            _content_type = models.ForeignKey(ContentType, editable=False)
            _object_id = models.PositiveIntegerField(editable=False)
            _content_object = generic.GenericForeignKey('_content_type', '_object_id')
            if options.use_sites:
                _site = models.ForeignKey(Site, null=True, blank=True)
            if options.use_i18n:
                _language = models.CharField(max_length=5, null=True, blank=True, db_index=True)
            objects = self.get_manager(options)()
        
            def __unicode__(self):
                return self._path

            class Meta:
                unique_together = self.get_unique_together(options)
                abstract = True

        return ModelInstanceMetadataBase


class ModelMetadataPlugin(MetadataPlugin):

    name = "model"
    unique_together = (("_content_type",),)

    def get_from_path(self, queryset, path):
        return queryset.get(_content_type=path)

    def get_model(self, options):
        class ModelMetadataBase(MetadataBaseModel):
            _content_type = models.ForeignKey(ContentType)
            if options.use_sites:
                _site = models.ForeignKey(Site, null=True, blank=True)
            if options.use_i18n:
                _language = models.CharField(max_length=5, null=True, blank=True, db_index=True)
            objects = self.get_manager(options)()

            def __unicode__(self):
                return unicode(self._content_type)

            def _set_context(self, instance):
                """ Use the given model instance as context for rendering 
                    any substitutions. 
                """
                self.__instance = instance
        
            def _resolve_value(self, name):
                value = super(ModelMetadataBase, self)._resolve_value(name)
                return _resolve(value, self.__instance)
        
            class Meta:
                abstract = True
                unique_together = self.get_unique_together(options)
        return ModelMetadataBase



def _resolve(value, model_instance=None, context=None):
    """ Resolves any template references in the given value. 
    """

    if isinstance(value, basestring) and "{" in value:
        if context is None:
            context = Context()
        if model_instance is not None:
            context[model_instance._meta.module_name] = model_instance
        value = Template(value).render(context)
    return value

def _get_seo_models(metadata):
    """ Gets the actual models to be used. """
    seo_models = []
    for model_name in metadata._meta.seo_models:
        if "." in model_name:
            app_label, model_name = model_name.split(".", 1)
            model = models.get_model(app_label, model_name)
            if model:
                seo_models.append(model)
        else:
            app = models.get_app(model_name)
            if app:
                seo_models.extend(models.get_models(app))

    return seo_models
