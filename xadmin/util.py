import django
from django.db import models
from django.db.models.sql.query import LOOKUP_SEP
from django.db.models.deletion import Collector

from django import get_version
v = get_version()
if v[:3] > '1.7':
    from django.db.models.fields.related import ForeignObjectRel
else:
    from django.db.models.related import RelatedObject as ForeignObjectRel
from django.contrib.auth import get_permission_codename
from django.forms.forms import pretty_name
from django.utils import formats
from django.utils.html import escape
from django.utils.safestring import mark_safe
from django.utils.text import capfirst
import sys
if sys.version_info.major < 3:
   from django.utils.encoding import force_unicode as force_text, smart_unicode as smart_text, smart_str as smart_bytes
else:
   from django.utils.encoding import force_text, smart_text, smart_bytes
from django.utils.translation import ungettext
from django.core.urlresolvers import reverse
from django.conf import settings
from django.forms import Media
from django.utils.translation import get_language
import datetime
import decimal


if 'django.contrib.staticfiles' in settings.INSTALLED_APPS:
    from django.contrib.staticfiles.templatetags.staticfiles import static
else:
    from django.templatetags.static import static

try:
    import json
except ImportError:
    from django.utils import simplejson as json

try:
    from django.utils.timezone import template_localtime as tz_localtime
except ImportError:
    from django.utils.timezone import localtime as tz_localtime

try:
    from django.contrib.auth import get_user_model
    User = get_user_model()
    username_field = User.USERNAME_FIELD
except Exception:
    from django.contrib.auth.models import User
    username_field = 'username'


def xstatic(*tags):
    from xadmin.vendors import vendors
    node = vendors

    fs = []
    lang = get_language()

    for tag in tags:
        try:
            for p in tag.split('.'):
                node = node[p]
        except Exception as e:
            if tag.startswith('xadmin'):
                file_type = tag.split('.')[-1]
                if file_type in ('css', 'js'):
                    node = str("xadmin/%s/%s" % (file_type, tag))
                else:
                    raise e
            else:
                raise e

        if type(node) in (str,):
            files = node
        else:
            mode = 'dev'
            if not settings.DEBUG:
                mode = getattr(settings, 'STATIC_USE_CDN',
                               False) and 'cdn' or 'production'

            if mode == 'cdn' and mode not in node:
                mode = 'production'
            if mode == 'production' and mode not in node:
                mode = 'dev'
            files = node[mode]

        files = type(files) in (list, tuple) and files or [files, ]
        fs.extend(files)

    return [f.startswith('http://') and f or static(f) for f in fs]


def vendor(*tags):
    media = Media()
    for tag in tags:
        file_type = tag.split('.')[-1]
        files = xstatic(tag)
        if file_type == 'js':
            media.add_js(files)
        elif file_type == 'css':
            media.add_css({'screen': files})
    return media


def lookup_needs_distinct(opts, lookup_path):
    """
    Returns True if 'distinct()' should be used to query the given lookup path.
    """
    if sys.version_info.major < 3:
        field_name = lookup_path.split(b'__', 1)[0]
        field = opts.get_field_by_name(str(field_name))[0]
    else:
        field_name = bytes(lookup_path, 'utf-8').split(b'__', 1)[0]
        field = opts.get_field_by_name(str(field_name, 'utf-8'))[0]
    if ((hasattr(field, 'rel') and
         isinstance(field.rel, models.ManyToManyRel)) or
        (isinstance(field, ForeignObjectRel) and
         not field.field.unique)):
        return True
    return False


def prepare_lookup_value(key, value):
    """
    Returns a lookup value prepared to be used in queryset filtering.
    """
    # if key ends with __in, split parameter into separate values
    if key.endswith('__in'):
        value = value.split(',')
    # if key ends with __isnull, special case '' and false
    if key.endswith('__isnull') and type(value) == str:
        if value.lower() in ('', 'false'):
            value = False
        else:
            value = True
    return value


def quote(s):
    """
    Ensure that primary key values do not confuse the admin URLs by escaping
    any '/', '_' and ':' characters. Similar to urllib.quote, except that the
    quoting is slightly different so that it doesn't get automatically
    unquoted by the Web browser.
    """
    if not isinstance(s, str):
        return s
    res = list(s)
    for i in range(len(res)):
        c = res[i]
        if c in """:/_#?;@&=+$,"<>%\\""":
            res[i] = '_%02X' % ord(c)
    return ''.join(res)


def unquote(s):
    """
    Undo the effects of quote(). Based heavily on urllib.unquote().
    """
    if not isinstance(s, str):
        return s
    mychr = chr
    myatoi = int
    list = s.split('_')
    res = [list[0]]
    myappend = res.append
    del list[0]
    for item in list:
        if item[1:2]:
            try:
                myappend(mychr(myatoi(item[:2], 16)) + item[2:])
            except ValueError:
                myappend('_' + item)
        else:
            myappend('_' + item)
    return "".join(res)


def flatten_fieldsets(fieldsets):
    """Returns a list of field names from an admin fieldsets structure."""
    field_names = []
    for name, opts in fieldsets:
        for field in opts['fields']:
            # type checking feels dirty, but it seems like the best way here
            if type(field) == tuple:
                field_names.extend(field)
            else:
                field_names.append(field)
    return field_names


def get_deleted_objects(objs, opts, user, admin_site, using):
    """
    Find all objects related to ``objs`` that should also be deleted. ``objs``
    must be a homogenous iterable of objects (e.g. a QuerySet).

    Returns a nested list of strings suitable for display in the
    template with the ``unordered_list`` filter.

    """
    collector = NestedObjects(using=using)
    collector.collect(objs)
    perms_needed = set()

    def format_callback(obj):
        has_admin = obj.__class__ in admin_site._registry
        opts = obj._meta

        if has_admin:
            admin_url = reverse('%s:%s_%s_change'
                                % (admin_site.name,
                                   opts.app_label,
                                   opts.object_name.lower()),
                                None, (quote(obj._get_pk_val()),))
            p = '%s.%s' % (opts.app_label,
                           get_permission_codename('delete', opts))
            if not user.has_perm(p):
                perms_needed.add(opts.verbose_name)
            # Display a link to the admin page.
            return mark_safe(u'<span class="label label-info">%s:</span> <a href="%s">%s</a>' %
                             (escape(capfirst(opts.verbose_name)),
                              admin_url,
                              escape(obj)))
        else:
            # Don't display link to edit, because it either has no
            # admin or is edited inline.
            return mark_safe(u'<span class="label label-info">%s:</span> %s' %
                             (escape(capfirst(opts.verbose_name)),
                              escape(obj)))

    to_delete = collector.nested(format_callback)
    protected = [format_callback(obj) for obj in collector.protected]

    return to_delete, perms_needed, protected


class NestedObjects(Collector):
    def __init__(self, *args, **kwargs):
        super(NestedObjects, self).__init__(*args, **kwargs)
        self.edges = {}  # {from_instance: [to_instances]}
        self.protected = set()

    def add_edge(self, source, target):
        self.edges.setdefault(source, []).append(target)

    def collect(self, objs, source_attr=None, **kwargs):
        for obj in objs:
            if source_attr:
                self.add_edge(getattr(obj, source_attr), obj)
            else:
                self.add_edge(None, obj)
        try:
            return super(NestedObjects, self).collect(objs, source_attr=source_attr, **kwargs)
        except models.ProtectedError as e:
            self.protected.update(e.protected_objects)

    def related_objects(self, related, objs):
        qs = super(NestedObjects, self).related_objects(related, objs)
        return qs.select_related(related.field.name)

    def _nested(self, obj, seen, format_callback):
        if obj in seen:
            return []
        seen.add(obj)
        children = []
        for child in self.edges.get(obj, ()):
            children.extend(self._nested(child, seen, format_callback))
        if format_callback:
            ret = [format_callback(obj)]
        else:
            ret = [obj]
        if children:
            ret.append(children)
        return ret

    def nested(self, format_callback=None):
        """
        Return the graph as a nested list.

        """
        seen = set()
        roots = []
        for root in self.edges.get(None, ()):
            roots.extend(self._nested(root, seen, format_callback))
        return roots


def model_format_dict(obj):
    """
    Return a `dict` with keys 'verbose_name' and 'verbose_name_plural',
    typically for use with string formatting.

    `obj` may be a `Model` instance, `Model` subclass, or `QuerySet` instance.

    """
    if isinstance(obj, (models.Model, models.base.ModelBase)):
        opts = obj._meta
    elif isinstance(obj, models.query.QuerySet):
        opts = obj.model._meta
    else:
        opts = obj
    return {
        'verbose_name': force_text(opts.verbose_name),
        'verbose_name_plural': force_text(opts.verbose_name_plural)
    }


def model_ngettext(obj, n=None):
    """
    Return the appropriate `verbose_name` or `verbose_name_plural` value for
    `obj` depending on the count `n`.

    `obj` may be a `Model` instance, `Model` subclass, or `QuerySet` instance.
    If `obj` is a `QuerySet` instance, `n` is optional and the length of the
    `QuerySet` is used.

    """
    if isinstance(obj, models.query.QuerySet):
        if n is None:
            n = obj.count()
        obj = obj.model
    d = model_format_dict(obj)
    singular, plural = d["verbose_name"], d["verbose_name_plural"]
    return ungettext(singular, plural, n or 0)

def is_rel_field(name,model):
    if hasattr(name,'split') and name.find("__")>0:
        parts = name.split("__")
        if parts[0] in model._meta.get_all_field_names():
            return True
    return False

def lookup_field(name, obj, model_admin=None):
    opts = obj._meta
    try:
        f = opts.get_field(name)
    except models.FieldDoesNotExist:
        # For non-field values, the value is either a method, property or
        # returned via a callable.
        if callable(name):
            attr = name
            value = attr(obj)
        elif (model_admin is not None and hasattr(model_admin, name) and
              not name == '__str__' and not name == '__unicode__'):
            attr = getattr(model_admin, name)
            value = attr(obj)
        else:
            if is_rel_field(name,obj):
                parts = name.split("__")
                rel_name,sub_rel_name = parts[0],"__".join(parts[1:])
                rel_obj =  getattr(obj,rel_name)
                if rel_obj is not None:
                    return lookup_field(sub_rel_name,rel_obj,model_admin)
            attr = getattr(obj, name)
            if callable(attr):
                value = attr()
            else:
                value = attr
        f = None
    else:
        attr = None
        value = getattr(obj, name)
    return f, attr, value


def label_for_field(name, model, model_admin=None, return_attr=False):
    """
    Returns a sensible label for a field name. The name can be a callable or the
    name of an object attributes, as well as a genuine fields. If return_attr is
    True, the resolved attribute (which could be a callable) is also returned.
    This will be None if (and only if) the name refers to a field.
    """
    attr = None
    try:
        field = model._meta.get_field_by_name(name)[0]
        if isinstance(field, ForeignObjectRel):
            label = field.opts.verbose_name
        else:
            label = field.verbose_name
    except models.FieldDoesNotExist:
        if name == "__unicode__":
            label = force_text(model._meta.verbose_name)
            attr = unicode
        elif name == "__str__":
            label = smart_bytes(model._meta.verbose_name)
            attr = str
        else:
            if callable(name):
                attr = name
            elif model_admin is not None and hasattr(model_admin, name):
                attr = getattr(model_admin, name)
            elif hasattr(model, name):
                attr = getattr(model, name)
            elif is_rel_field(name,model):
                parts = name.split("__")
                rel_name,name = parts[0],"__".join(parts[1:])
                field = model._meta.get_field_by_name(rel_name)[0]
                if isinstance(field, ForeignObjectRel):
                    label = field.opts.verbose_name
                else:
                    label = field.verbose_name

                rel_model = field.rel.to
                rel_label = label_for_field(name, rel_model, model_admin=model_admin, return_attr=return_attr)

                if return_attr:
                    rel_label,attr = rel_label
                    return ("%s %s"%(label,rel_label), attr)
                else:
                    return "%s %s"%(label,rel_label)
            else:
                message = "Unable to lookup '%s' on %s" % (
                    name, model._meta.object_name)
                if model_admin:
                    message += " or %s" % (model_admin.__class__.__name__,)
                raise AttributeError(message)

            if hasattr(attr, "short_description"):
                label = attr.short_description
            elif callable(attr):
                if attr.__name__ == "<lambda>":
                    label = "--"
                else:
                    label = pretty_name(attr.__name__)
            else:
                label = pretty_name(name)
    if return_attr:
        return (label, attr)
    else:
        return label


def help_text_for_field(name, model):
    try:
        help_text = model._meta.get_field_by_name(name)[0].help_text
    except models.FieldDoesNotExist:
        help_text = ""
    return smart_text(help_text)


def admin_urlname(value, arg):
    return 'xadmin:%s_%s_%s' % (value.app_label, value.model_name, arg)


def boolean_icon(field_val):
    return mark_safe(u'<i class="%s" alt="%s"></i>' % (
        {True: 'fa fa-check-circle text-success', False: 'fa fa-times-circle text-error', None: 'fa fa-question-circle muted'}[field_val], field_val))


def collor_field(field_val):
    format_str = """
    <div class="sp-replacer sp-light sp-disabled">
        <div class="sp-preview" style="margin-right: 0;">
            <div class="sp-preview-inner" style="background-color: %s;"></div>
        </div>
    </div>
    """
    return mark_safe(format_str % field_val)


def image_field(image, field, **kwargs):
    if image:
        if 'show_thumb' in kwargs and kwargs['show_thumb']:
            label = label_for_field(field.name, field.model)
            small = field.get_small(image)
            medium = field.get_medium(image)
            format_str = """
            <a href="%s" target="_blank" title="%s" data-gallery="gallery" data-download="%s"><img src="%s" class="field_img"/></a>
            """
            return mark_safe(format_str % (medium.url, label, image.url, small.url))
    return smart_text(image)


def coordinates_field(value, field, model_admin):
    if value:
        import re
        match = re.search(r"\((.*)\)", str(value))
        lonlat = match.group(1).split( );
        if model_admin:
            inputs_str = ''
            if field.show_in_map:
                for item in field.show_in_map:
                    try:
                        inputs_str += '<input type="hidden" class="show_in_map" url="/%(app)s/%(model)s/inmap/%(field)s/" icon="%(icon)s" zoom="%(zoom)s" />' % item
                    except:
                        #TODO: raise error
                        pass

            format_str = """
            <span id="id_%s" class="openstreetmap_view" style="width:100%%;height:350px;" point="%s" center="%s" zoom="%s">%s:%s%s</span>"""
            options = (
                field.name,
                value,
                settings.OSM_COORDINATES_CENTER if hasattr(settings, 'OSM_COORDINATES_CENTER') else '',
                str(settings.OSM_COORDINATES_ZOOM) if hasattr(settings, 'OSM_COORDINATES_ZOOM') else '',
                lonlat[1],
                lonlat[0],
                inputs_str
            )
            return mark_safe(format_str % (options))
        else:
            return "%s:%s" % (lonlat[1], lonlat[0])
    return ''


def display_for_field(value, field , model_admin=None, **kwargs):
    from xadmin.views.list import EMPTY_CHANGELIST_VALUE
    from xadmin.fields import ColorField, ImageWithThumbField, CoordinatesField

    if field.flatchoices:
        return dict(field.flatchoices).get(value, EMPTY_CHANGELIST_VALUE)
    # NullBooleanField needs special-case null-handling, so it comes
    # before the general null test.
    elif isinstance(field, models.BooleanField) or isinstance(field, models.NullBooleanField):
        return boolean_icon(value)
    elif value is None:
        return EMPTY_CHANGELIST_VALUE
    elif isinstance(field, models.DateTimeField):
        return formats.localize(tz_localtime(value))
    elif isinstance(field, (models.DateField, models.TimeField)):
        return formats.localize(value)
    elif isinstance(field, models.DecimalField):
        return formats.number_format(value, field.decimal_places)
    elif isinstance(field, models.FloatField):
        return formats.number_format(value)
    elif isinstance(field.rel, models.ManyToManyRel):
        return ', '.join([smart_text(obj) for obj in value.all()])
    elif isinstance(field, ColorField):
        return collor_field(value)
    elif isinstance(field, CoordinatesField):
        return coordinates_field(value, field, model_admin)
    elif isinstance(field, ImageWithThumbField):
        return image_field(value, field, **kwargs)
    else:
        return smart_text(value)


def display_for_value(value, boolean=False):
    from xadmin.views.list import EMPTY_CHANGELIST_VALUE

    if boolean:
        return boolean_icon(value)
    elif value is None:
        return EMPTY_CHANGELIST_VALUE
    elif isinstance(value, datetime.datetime):
        return formats.localize(tz_localtime(value))
    elif isinstance(value, (datetime.date, datetime.time)):
        return formats.localize(value)
    elif isinstance(value, (decimal.Decimal, float)):
        return formats.number_format(value)
    else:
        return smart_text(value)


class NotRelationField(Exception):
    pass


def get_model_from_relation(field):
    if hasattr(field, 'related_model'):
        return field.related_model
    elif isinstance(field, ForeignObjectRel):
        return field.model
    elif getattr(field, 'rel'):  # or isinstance?
        return field.rel.to
    else:
        raise NotRelationField


def reverse_field_path(model, path):
    """ Create a reversed field path.

    E.g. Given (Order, "user__groups"),
    return (Group, "user__order").

    Final field must be a related model, not a data field.

    """
    reversed_path = []
    parent = model
    pieces = path.split(LOOKUP_SEP)
    for piece in pieces:
        field, model, direct, m2m = parent._meta.get_field_by_name(piece)
        # skip trailing data field if extant:
        if len(reversed_path) == len(pieces) - 1:  # final iteration
            try:
                get_model_from_relation(field)
            except NotRelationField:
                break
        if direct:
            related_name = field.related_query_name()
            parent = field.rel.to
        else:
            related_name = field.field.name
            parent = field.model
        reversed_path.insert(0, related_name)
    return (parent, LOOKUP_SEP.join(reversed_path))


def get_fields_from_path(model, path):
    """ Return list of Fields given path relative to model.

    e.g. (ModelX, "user__groups__name") -> [
        <django.db.models.fields.related.ForeignKey object at 0x...>,
        <django.db.models.fields.related.ManyToManyField object at 0x...>,
        <django.db.models.fields.CharField object at 0x...>,
    ]
    """
    pieces = path.split(LOOKUP_SEP)
    fields = []
    for piece in pieces:
        if fields:
            parent = get_model_from_relation(fields[-1])
        else:
            parent = model
        fields.append(parent._meta.get_field_by_name(piece)[0])
    return fields


def remove_trailing_data_field(fields):
    """ Discard trailing non-relation field if extant. """
    try:
        get_model_from_relation(fields[-1])
    except NotRelationField:
        fields = fields[:-1]
    return fields


def get_limit_choices_to_from_path(model, path):
    """ Return Q object for limiting choices if applicable.

    If final model in path is linked via a ForeignKey or ManyToManyField which
    has a `limit_choices_to` attribute, return it as a Q object.
    """

    fields = get_fields_from_path(model, path)
    fields = remove_trailing_data_field(fields)
    limit_choices_to = (
        fields and hasattr(fields[-1], 'rel') and
        getattr(fields[-1].rel, 'limit_choices_to', None))
    if not limit_choices_to:
        return models.Q()  # empty Q
    elif isinstance(limit_choices_to, models.Q):
        return limit_choices_to  # already a Q
    else:
        return models.Q(**limit_choices_to)  # convert dict to Q


def sortkeypicker(keynames):
    negate = set()
    for i, k in enumerate(keynames):
        if k[:1] == '-':
            keynames[i] = k[1:]
            negate.add(k[1:])
    def getit(adict):
        composite = [adict[k] for k in keynames]
        for i, (k, v) in enumerate(zip(keynames, composite)):
            if k in negate:
                composite[i] = -v
        return composite
    return getit
