#define PY_SSIZE_T_CLEAN
#include <pyconfig.h>

#ifdef PARSER_PY_LIMITED_API
/* Py_LIMITED_API isn't compatible with Py_TRACE_REFS */
#  if !defined(Py_TRACE_REFS)
#    define Py_LIMITED_API PARSER_PY_LIMITED_API
#  endif
#  define PARSER_PY_VERSION_HEX PARSER_PY_LIMITED_API
#else
#  define PARSER_PY_VERSION_HEX PY_VERSION_HEX
#endif

#include <Python.h>
#include <structmember.h>
#include <expat.h>
#include <assert.h>
#include <stdarg.h>

#pragma GCC diagnostic ignored "-Wunused-parameter"

#define STACK_BLOCK_SIZE 100
#define NODE_LIST_INITIAL_CAPACITY 5

#ifndef MODULE_NAME
#define MODULE_NAME parser
#endif

#ifndef FULL_MODULE_STR
#define FULL_MODULE_STR Py_STRINGIFY(MODULE_NAME)
#endif

#define _MAKE_INIT_FUN_NAME(END) PyInit_##END
#define MAKE_INIT_FUN_NAME(END) _MAKE_INIT_FUN_NAME(END)

#define EXPAT_BUFFER_SIZE 0x1000

/* According to the Expat documentation, the "len" argument passed to XML_Parse
must be "considerably less than the maximum value for an integer", so long input
is broken into chunks. */
#define EXPAT_CHUNK_SIZE (1<<20)

#ifndef Py_TPFLAGS_SEQUENCE
#define Py_TPFLAGS_SEQUENCE 0
#endif

#if PARSER_PY_VERSION_HEX >= 0x03090000
#define COMPAT_Py_GenericAlias Py_GenericAlias
#else
/* Before Python 3.9, there was no types.GenericAlias class, so just return the
class unchanged */
static PyObject *COMPAT_Py_GenericAlias(PyObject *cls, PyObject *Py_UNUSED(val)) {
    Py_INCREF(cls);
    return cls;
}
#endif


enum {
    CLASS_NODE = 0, /* important: module_exec() assumes this comes before CLASS_FROZEN_LIST */
    CLASS_FROZEN_LIST,
    CLASS_FROZEN_LIST_ITR,
    CLASS_TAGGED_VALUE,
    CLASS_PARSE_ERROR,
    CLASS_PARSE_WARNING,
//% for type in types|select('used_directly')
    CLASS__{$ type $},
//% endfor
//% for type in types|select('content_tuple')
    CLASS_ITEM__{$ type $},
//% endfor
    CLASS_COUNT
};

//% for type in types|select('enumeration_t')
/* it's ENUM_VALUE because it refers to a Python enum value */
enum {
//% for value in type.children
    ENUM_VALUE__{$ type $}__{$ value.id $}{$ ' = 0' if loop.first $},
//% endfor
    ENUM_VALUE_COUNT__{$ type $}
};
//% endfor

typedef enum {
//%   for n in union_tag_names
    TAGGED_UNION_NAME__{$ n $}{$ ' = 0' if loop.first $},
//%   endfor
    TAGGED_UNION_NAME_COUNT
} tagged_union_name_t;

//% if char_enum_chars
const char enum_chars[] = {{% for c in char_enum_chars %}'{$ c $}'{$ ',' if not loop.last $}{% endfor %}};

enum {
//% for c in char_enum_chars
    ENUM_CHAR__{$ c $}{$ ' = 0' if loop.first $},
//% endfor
    ENUM_CHAR_COUNT
};
//% endif

static PyModuleDef module_def;

typedef struct {
    PyObject *tag_names[TAGGED_UNION_NAME_COUNT];

//% if char_enum_chars
    PyObject *char_objects[ENUM_CHAR_COUNT];
//% endif

    /* an array of record-like classes */
    PyTypeObject *classes[CLASS_COUNT];

//% for type in types|select('enumeration_t')
    PyObject *enum_values__{$ type $}[ENUM_VALUE_COUNT__{$ type $}];
//% endfor
} module_state;

struct _parse_state;
struct _parse_callbacks;

typedef int (*child_start_callback)(struct _parse_state*,const XML_Char*,const XML_Char**);
typedef int (*finish_callback)(struct _parse_state*);
typedef int (*text_callback)(struct _parse_state*,const XML_Char*,int);

typedef struct _parse_callbacks {
    PyObject **value;
    child_start_callback cs_call;
    text_callback t_call;
    finish_callback f_call;
} parse_callbacks;

typedef struct _callback_stack_block {
    struct _callback_stack_block *prev;
    struct _callback_stack_block *next;
    parse_callbacks stack[STACK_BLOCK_SIZE];
} callback_stack_block;

typedef struct _parse_state {
    callback_stack_block *block;
    unsigned int block_used;
    XML_Parser parser;
    int ignore_level;
    module_state *py;
} parse_state;

static parse_callbacks *push_callbacks(parse_state *state) {
    if(state->block_used == STACK_BLOCK_SIZE) {
        if(state->block->next == NULL) {
            state->block->next = PyMem_Malloc(sizeof(callback_stack_block));
            if(state->block->next == NULL) return NULL;
            state->block->next->next = NULL;
            state->block->next->prev = state->block;
        }
        state->block = state->block->next;
        state->block_used = 0;
    }
    ++state->block_used;
    return &state->block->stack[state->block_used-1];
}

static parse_callbacks *top_callbacks(parse_state *state) {
    assert(state->block_used);
    return &state->block->stack[state->block_used-1];
}

static void pop_callbacks(parse_state *state) {
    assert(state->block_used);
    --state->block_used;
    if(state->block_used == 0 && state->block->prev) {
        state->block = state->block->prev;
        state->block_used = STACK_BLOCK_SIZE;
    }
}

static void set_parse_error(parse_state *state,const char *msg) {
    PyObject *exc = PyObject_CallFunction(
        (PyObject*)state->py->classes[CLASS_PARSE_ERROR],
        "sl",
        XML_ErrorString(XML_GetErrorCode(state->parser)),
        (long)XML_GetCurrentLineNumber(state->parser));
    if(exc == NULL) return;
    PyErr_SetObject((PyObject*)state->py->classes[CLASS_PARSE_ERROR],exc);
    Py_DECREF(exc);
}
static void set_parse_error_format(parse_state *state,const char *msg,...) {
    PyObject *msg_obj;
    PyObject *exc;
    va_list vargs;
    
    va_start(vargs,msg);
    msg_obj = PyUnicode_FromFormatV(msg,vargs);
    va_end(vargs);
    if(msg_obj == NULL) return;

    exc = PyObject_CallFunction(
        (PyObject*)state->py->classes[CLASS_PARSE_ERROR],
        "Ol",
        msg_obj,
        (long)XML_GetCurrentLineNumber(state->parser));
    Py_DECREF(msg_obj);
    if(exc == NULL) return;
    PyErr_SetObject((PyObject*)state->py->classes[CLASS_PARSE_ERROR],exc);
    Py_DECREF(exc);
}

static int set_parse_warning(parse_state *state,const char *msg) {
    return PyErr_WarnFormat(
        (PyObject*)state->py->classes[CLASS_PARSE_WARNING],
        1,
        "Warning on line %li: %s",
        (long)XML_GetCurrentLineNumber(state->parser),
        msg);
}
#define SET_PARSE_WARNING_FMT(state,msg,...) \
    PyErr_WarnFormat(\
        (PyObject*)state->py->classes[CLASS_PARSE_WARNING],\
        1,\
        "Warning on line %li: " msg,\
        (long)XML_GetCurrentLineNumber(state->parser),\
        __VA_ARGS__)

static void XMLCALL start_element(void *user,const XML_Char *name,const XML_Char **attrs) {
    parse_state *state = (parse_state*)user;
    parse_callbacks *pc = top_callbacks(state);

    if(state->ignore_level) {
        ++state->ignore_level;
        return;
    }

    if(pc->cs_call != NULL) {
        int r = pc->cs_call(state,name,attrs);
        if(r < 0) {
            XML_StopParser(state->parser,XML_FALSE);
            return;
        } else if(r) return;
    }

    if(SET_PARSE_WARNING_FMT(state,"unexpected element \"%s\"",name)) {
        XML_StopParser(state->parser,XML_FALSE);
    } else {
        state->ignore_level = 1;
    }
}

static void XMLCALL end_element(void *user,const XML_Char *Py_UNUSED(name)) {
    parse_state *state = (parse_state*)user;
    parse_callbacks *pc = top_callbacks(state);

    if(state->ignore_level) {
        --state->ignore_level;
        return;
    }

    if(pc->f_call != NULL && pc->f_call(state)) XML_StopParser(state->parser,XML_FALSE);
    pop_callbacks(state);
}

int non_whitespace(const char *s,int len) {
    int i;
    for(i=0; i<len; ++i) {
        switch(*s) {
        case ' ':
        case '\t':
        case '\n':
        case '\r':
        case '\v':
            ++s;
            break;
        case '\0':
            return len >= 0;
        default:
            return 1;
        }
    }
    return 0;
}

static void XMLCALL character_data(void *user,const XML_Char *s,int len) {
    parse_state *state = (parse_state*)user;
    parse_callbacks *pc = top_callbacks(state);

    if(state->ignore_level) return;

    if(pc->t_call != NULL) {
        if(pc->t_call(state,s,len)) XML_StopParser(state->parser,XML_FALSE);
    } else if(non_whitespace(s,len)) {
        if(set_parse_warning(state,"unexpected character data")) {
            XML_StopParser(state->parser,XML_FALSE);
        }
    }
}

static int find_str_in_array(const char *str,const char *array[],int a_size) {
    for(int i=0; i<a_size; ++i) {
        if(strcmp(str,array[i]) == 0) return i;
    }
    return -1;
}

static int visit_array(PyObject **objs,size_t size,visitproc visit,void *arg) {
    while(size-- > 0) Py_VISIT(objs[size]);
    return 0;
}

/* Creating a type with PyStructSequence_NewType would have been preferable, but
there is no way to get the address of a value inside a native Python object
using the stable ABI. The parser stack requires the address of where the current
value being parsed, will be placed, so that extra code isn't needed for the end
of each child element to move the value to the correct spot. */
typedef struct {
    PyObject_HEAD
    PyObject *values[2];
} tagged_value;

static void init_tagged_value(tagged_value *tv) {
    tv->values[0] = NULL;
    tv->values[1] = NULL;
}
static tagged_value *create_tagged_value(module_state *state) {
    tagged_value *r = PyObject_GC_New(tagged_value,state->classes[CLASS_TAGGED_VALUE]);
    if(r != NULL) init_tagged_value(r);
    return r;
}

static void tagged_value_dealloc(tagged_value *tv) {
    PyTypeObject *t = Py_TYPE(tv);
    PyObject_GC_UnTrack(tv);
    Py_CLEAR(tv->values[0]);
    Py_CLEAR(tv->values[1]);
    ((freefunc)PyType_GetSlot(t,Py_tp_free))(tv);
    Py_DECREF(t);
}
static int tagged_value_traverse(tagged_value *tv,visitproc visit,void *arg) {
    Py_VISIT(tv->values[0]);
    Py_VISIT(tv->values[1]);
    return 0;
}

static Py_ssize_t tagged_value_size(PyObject *Py_UNUSED(obj)) {
    return 2;
}

static PyObject *tagged_value_item(tagged_value *tv,Py_ssize_t i) {
    if(i < 0 || i > 1) {
        PyErr_SetString(PyExc_IndexError,"index out of range");
        return NULL;
    }
    Py_INCREF(tv->values[i]);
    return tv->values[i];
}

static PyMemberDef tagged_value_members[] = {
    {"name",T_OBJECT_EX,offsetof(tagged_value,values),READONLY,NULL},
    {"value",T_OBJECT_EX,offsetof(tagged_value,values) + sizeof(PyObject*),READONLY,NULL},
    {NULL}};

static PyObject *tagged_value_tp_new(PyTypeObject *subtype,PyObject *args,PyObject *kwds) {
    tagged_value *r;
    if(kwds != NULL && PyDict_Size(kwds)) {
        PyErr_SetString(PyExc_TypeError,"TaggedValue.__new__ does not take any keyword arguments");
        return NULL;
    }
    if(PyTuple_Size(args) != 2) {
        PyErr_SetString(PyExc_TypeError,"TaggedValue.__new__ takes exactly two arguments");
        return NULL;
    }

    r = (tagged_value*)((allocfunc)PyType_GetSlot(subtype,Py_tp_alloc))(subtype,0);
    if(r == NULL) return NULL;

    r->values[0] = PyTuple_GetItem(args,0);
    Py_INCREF(r->values[0]);
    r->values[1] = PyTuple_GetItem(args,1);
    Py_INCREF(r->values[1]);

    return (PyObject*)r;
}

static PyMethodDef tagged_value_methods[] = {
    {"__class_getitem__", COMPAT_Py_GenericAlias, METH_O|METH_CLASS, PyDoc_STR("See PEP 585")},
    {NULL}
};

static PyType_Slot tagged_value_slots[] = {
    {Py_tp_new,tagged_value_tp_new},
    {Py_tp_members,tagged_value_members},
    {Py_tp_dealloc,tagged_value_dealloc},
    {Py_sq_length,tagged_value_size},
    {Py_sq_item,tagged_value_item},
    {Py_tp_traverse,tagged_value_traverse},
    {Py_tp_methods,tagged_value_methods},
    {0,NULL}};


typedef struct {
    PyObject_HEAD
    size_t size;
    size_t capacity;
    PyObject **content;
} frozen_list;

/* A type doesn't satisfy collections.abc.Iterable unless it has an __iter__
method */
typedef struct {
    PyObject_HEAD
    size_t i;
    frozen_list *fl;
} frozen_list_itr;

static void init_frozen_list(frozen_list *fl) {
    fl->size = 0;
    fl->capacity = 0;
    fl->content = NULL;
}

static frozen_list *create_frozen_list(module_state *state) {
    frozen_list *r = PyObject_GC_New(frozen_list,state->classes[CLASS_FROZEN_LIST]);
    if(r) init_frozen_list(r);
    return r;
}

/* This steals a reference to 'o'. 'o' can also be NULL to reserve a spot. */
static int frozen_list_push_object(frozen_list *fl,PyObject *o,size_t initial_cap) {
    assert(fl->size <= fl->capacity);

    if(fl->size == fl->capacity) {
        PyObject **tmp;
        size_t new_cap = fl->capacity * 2;
        if(fl->capacity == 0) new_cap = initial_cap;
        tmp = PyMem_Realloc(fl->content,new_cap * sizeof(PyObject*));
        if(tmp == NULL) {
            PyErr_NoMemory();
            return -1;
        }
        fl->capacity = new_cap;
        fl->content = tmp;
    }
    fl->content[fl->size++] = o;
    return 0;
}

static PyObject **frozen_list_push_tagged_value(module_state *state,tagged_union_name_t name,frozen_list *fl,size_t initial_cap) {
    tagged_value *tv = create_tagged_value(state);
    if(tv == NULL) return NULL;
    if(frozen_list_push_object(fl,(PyObject*)tv,initial_cap)) {
        Py_DECREF(tv);
        return NULL;
    }
    tv->values[0] = state->tag_names[name];
    Py_INCREF(tv->values[0]);
    return &tv->values[1];
}

static void frozen_list_dealloc(frozen_list *obj) {
    size_t i;
    size_t size = obj->size;
    PyTypeObject *t = Py_TYPE(obj);
    PyObject_GC_UnTrack(obj);
    obj->size = 0;
    for(i=0; i<size; ++i) Py_XDECREF(obj->content[i]);
    if(obj->content) PyMem_Free(obj->content);
    ((freefunc)PyType_GetSlot(t,Py_tp_free))(obj);
    Py_DECREF(t);
}
static int frozen_list_traverse(frozen_list *obj,visitproc visit,void *arg) {
    return visit_array(obj->content,obj->size,visit,arg);
}

static Py_ssize_t frozen_list_size(frozen_list *fl) {
    return (Py_ssize_t)fl->size;
}

static PyObject *frozen_list_item(frozen_list *fl,Py_ssize_t i) {
    if(i < 0 || (size_t)i >= fl->size) {
        PyErr_SetString(PyExc_IndexError,"index out of range");
        return NULL;
    }
    Py_INCREF(fl->content[i]);
    return fl->content[i];
}

static int frozen_list_fill(frozen_list *fl,PyObject *iterable) {
    PyObject *itr;
    PyObject *tmp;
    Py_ssize_t initial_size = PyObject_Size(iterable);
    if(initial_size < 0) {
        PyErr_Clear();
        initial_size = NODE_LIST_INITIAL_CAPACITY;
    }
    if(initial_size == 0) return 0;

    itr = PyObject_GetIter(iterable);
    if(itr == NULL) return -1;
    while((tmp = PyIter_Next(itr))) {
        if(frozen_list_push_object(fl,tmp,(size_t)initial_size)) {
            Py_DECREF(tmp);
            Py_DECREF(itr);
            return -1;
        }
    }
    Py_DECREF(itr);
    if(PyErr_Occurred()) return -1;
    return 0;
}

static void raise_no_keyword_allowed(const char *func) {
    PyErr_Format(PyExc_TypeError,"%s does not take any keyword arguments",func);
}

static PyObject *frozen_list_tp_new(PyTypeObject *subtype,PyObject *args,PyObject *kwds) {
    frozen_list *r;
    if(kwds != NULL && PyDict_Size(kwds)) {
        raise_no_keyword_allowed("FrozenList.__new__");
        return NULL;
    }
    if(PyTuple_Size(args) != 1) {
        PyErr_SetString(PyExc_TypeError,"FrozenList.__new__ takes exactly one argument");
        return NULL;
    }

    r = (frozen_list*)((allocfunc)PyType_GetSlot(subtype,Py_tp_alloc))(subtype,0);
    if(r == NULL) return NULL;

    init_frozen_list(r);
    if(frozen_list_fill(r,PyTuple_GetItem(args,0))) {
        Py_DECREF(r);
        return NULL;
    }
    return (PyObject*)r;
}

static PyObject *frozen_list_tp_iter(frozen_list *self) {
    PyObject *m;
    frozen_list_itr *r;
    
    m = PyState_FindModule(&module_def);
    assert(m);
    r = PyObject_GC_New(frozen_list_itr,((module_state*)PyModule_GetState(m))->classes[CLASS_FROZEN_LIST_ITR]);
    if(r == NULL) return NULL;
    r->i = 0;
    r->fl = NULL;
    if(self->size) {
        r->fl = self;
        Py_INCREF(self);
    }
    return (PyObject*)r;
}

static PyMethodDef frozen_list_methods[] = {
    {"__class_getitem__", COMPAT_Py_GenericAlias, METH_O|METH_CLASS, PyDoc_STR("See PEP 585")},
    {NULL}
};


static PyType_Slot frozen_list_slots[] = {
    {Py_tp_iter,frozen_list_tp_iter},
    {Py_tp_new,frozen_list_tp_new},
    {Py_tp_dealloc,frozen_list_dealloc},
    {Py_sq_length,frozen_list_size},
    {Py_sq_item,frozen_list_item},
    {Py_tp_traverse,frozen_list_traverse},
    {Py_tp_methods,frozen_list_methods},
    {0,NULL}
};

static PyObject *frozen_list_itr_tp_iter(PyObject *self) {
    Py_INCREF(self);
    return self;
}

static PyObject *frozen_list_itr_tp_next(frozen_list_itr *self) {
    PyObject *r;

    if(self->fl == NULL) return NULL;

    assert(self->i < self->fl->size);
    r = self->fl->content[self->i++];
    if(self->i == self->fl->size) Py_CLEAR(self->fl);
    Py_INCREF(r);
    return r;
}

static PyObject *frozen_list_itr_length_hint(frozen_list_itr *self,PyObject *Py_UNUSED(x)) {
    if(self->fl == NULL) return PyLong_FromLong(0);
    return PyLong_FromLong(self->fl->size - self->i);
}

static PyMethodDef frozen_list_itr_methods[] = {
    {"__length_hint__",(PyCFunction)frozen_list_itr_length_hint,METH_NOARGS,NULL},
    {"__class_getitem__", COMPAT_Py_GenericAlias, METH_O|METH_CLASS, PyDoc_STR("See PEP 585")},
    {NULL}
};

static void frozen_list_itr_dealloc(frozen_list_itr *obj) {
    PyTypeObject *t = Py_TYPE(obj);
    PyObject_GC_UnTrack(obj);
    Py_CLEAR(obj->fl);
    ((freefunc)PyType_GetSlot(t,Py_tp_free))(obj);
    Py_DECREF(t);
}

static int frozen_list_itr_traverse(frozen_list_itr *obj,visitproc visit,void *arg) {
    if(obj->fl == NULL) return 0;
    return visit((PyObject*)obj->fl,arg);
}

static PyType_Slot frozen_list_itr_slots[] = {
    {Py_tp_iter,frozen_list_itr_tp_iter},
    {Py_tp_iternext,frozen_list_itr_tp_next},
    {Py_tp_methods,frozen_list_itr_methods},
    {Py_tp_dealloc,frozen_list_itr_dealloc},
    {Py_tp_traverse,frozen_list_itr_traverse},
    {0,NULL}
};

static PyObject *parse_error_get_args(PyObject *self) {
    PyObject *args = PyObject_GetAttrString(self,"args");
    if(args == NULL) return NULL;
    if(!PyTuple_Check(args) || PyTuple_Size(args) < 2) {
        PyErr_SetString(PyExc_TypeError,"\"self.args\" is supposed to be a tuple with a length of 2");
        Py_DECREF(args);
        return NULL;
    }
    return args;
}

static PyObject *parse_error_tp_str(PyObject *self) {
    PyObject *r;
    PyObject *lineno;
    PyObject *args = parse_error_get_args(self);
    if(args == NULL) return NULL;
    lineno = PyTuple_GetItem(args,1);
    if(lineno == Py_None) r = PyUnicode_FromFormat("Error: %S",lineno,PyTuple_GetItem(args,0));
    else r = PyUnicode_FromFormat("Error on line %S: %S",lineno,PyTuple_GetItem(args,0));
    Py_DECREF(args);
    return r;
}

static PyObject *parse_error_get(PyObject *self,void *i) {
    PyObject *r;
    PyObject *args = parse_error_get_args(self);
    if(args == NULL) return NULL;
    r = PyTuple_GetItem(args,(Py_ssize_t)i);
    Py_INCREF(r);
    return r;
}

static PyGetSetDef parse_error_getset[] = {
    {"message",parse_error_get,NULL,NULL,(void*)0},
    {"lineno",parse_error_get,NULL,NULL,(void*)1},
    {NULL}
};

static PyType_Slot parse_error_slots[] = {
    {Py_tp_str,parse_error_tp_str},
    {Py_tp_getset,parse_error_getset},
    {0,NULL}
};

static PyType_Slot parse_warning_slots[] = {
    {0,NULL}
};


typedef enum {
//% for n in element_names
    ELEMENT__{$ n $}{$ ' = 0' if loop.first $},
//% endfor
    ELEMENT_COUNT,
    ELEMENT_OTHER
} element_type;

static const char *element_names[] = {
//% for n in element_names
    "{$ n $}",
//% endfor
};

typedef enum {
//% for n in attribute_names
    ATTRIBUTE__{$ n $}{$ ' = 0' if loop.first $},
//% endfor
    ATTRIBUTE_COUNT,
    ATTRIBUTE_OTHER
} attribute_type;

typedef enum {
//% for n in py_field_names
    PY_FIELD__{$ n $}{$ ' = 0' if loop.first $},
//% endfor
    PY_FIELD_COUNT,
    PY_FIELD_OTHER
} py_field_type;

static const char *attribute_names[] = {
//% for n in attribute_names
    "{$ n $}"{$ ',' if not loop.last $}
//% endfor
};

static const char *tagged_union_names[] = {
//%   for n in union_tag_names
    "{$ n $}"{$ ',' if not loop.last $}
//%   endfor
};

static const char *py_field_names[] = {
//%       for n in py_field_names
    "{$ n $}"{$ ',' if not loop.last $}
//%       endfor
};

typedef struct {
    unsigned int length;
    const py_field_type *fields;
} field_set;

//% macro hash_lookup(f_name,hash_info,count,names,type,default)
static {$ type $} {$ f_name $}(const char *key) {
//%   if hash_info
    static const int salt_length = {$ hash_info.salt1|length $};
    static const int g_length = {$ hash_info.g|length $};
    static const int g[] = {
{$ hash_info.g|join(', ')|wordwrap $}};
    int f1 = 0, f2 = 0, i;

    for(i=0; key[i] && i<salt_length; i++) {
        f1 += "{$ hash_info.salt1 $}"[i] * key[i];
        f2 += "{$ hash_info.salt2 $}"[i] * key[i];
    }
    i = (g[f1 % g_length] + g[f2 % g_length]) % g_length;
    if(i < {$ count $} && strcmp(key, {$ names $}[i]) == 0) return {$ '('~type~')' if type != 'int' $}i;

    return {$ default $};
//%   else
    return find_str_in_array(key,{$ names $},{$ count $});
//%   endif
}
//% endmacro
{$ hash_lookup('element_lookup',e_hash,'ELEMENT_COUNT','element_names','element_type','ELEMENT_OTHER') $}
{$ hash_lookup('attribute_lookup',a_hash,'ATTRIBUTE_COUNT','attribute_names','attribute_type','ATTRIBUTE_OTHER') $}

/* this assumes all of py_field_names are ASCII strings */
static py_field_type py_field_lookup(PyObject *key) {
    static const int salt_length = {$ py_f_hash.salt1|length $};
    static const int g_length = {$ py_f_hash.g|length $};
    static const int g[] = {
{$ py_f_hash.g|join(', ')|wordwrap $}};
    int f1 = 0, f2 = 0, i;
    Py_UCS4 buffer[{$ py_f_hash.salt1|length $}];
    Py_ssize_t key_len = PyUnicode_GetLength(key);

    if(key_len > salt_length) return PY_FIELD_OTHER;

#ifdef NDEBUG
    PyUnicode_AsUCS4(key,buffer,key_len,0);
#else
    {
        Py_UCS4 *r = PyUnicode_AsUCS4(key,buffer,key_len,0);
        assert(r);
    }
#endif

    for(i=0; i<key_len; i++) {
        if(buffer[i] > 127) return PY_FIELD_OTHER;
        f1 += "{$ py_f_hash.salt1 $}"[i] * (int)buffer[i];
        f2 += "{$ py_f_hash.salt2 $}"[i] * (int)buffer[i];
    }
    i = (g[f1 % g_length] + g[f2 % g_length]) % g_length;

    if(i < PY_FIELD_COUNT && PyUnicode_CompareWithASCIIString(key,py_field_names[i]) == 0) return (py_field_type)i;

    return PY_FIELD_OTHER;
}

typedef struct {
    PyObject_HEAD
    PyObject *fields[1];
} node_tagonly_common;

static void init_node_tagonly_common(node_tagonly_common *n,size_t fields) {
    size_t i;
    for(i=0; i<fields; ++i) n->fields[i] = NULL;
}
static node_tagonly_common *create_node_tagonly_common(module_state *state,size_t class_index,size_t fields) {
    node_tagonly_common *r = PyObject_GC_New(node_tagonly_common,state->classes[class_index]);
    if(r) init_node_tagonly_common(r,fields);
    return r;
}

static void node_tagonly_common_dealloc(node_tagonly_common *obj,size_t field_count) {
    size_t i;
    PyTypeObject *t = Py_TYPE(obj);
    PyObject_GC_UnTrack(obj);
    for(i=0; i<field_count; ++i) Py_CLEAR(obj->fields[i]);
    ((freefunc)PyType_GetSlot(t,Py_tp_free))(obj);
    Py_DECREF(t);
}
static int node_tagonly_common_traverse(node_tagonly_common *obj,visitproc visit,void *arg,size_t field_count) {
    return visit_array(obj->fields,field_count,visit,arg);
}

//% for count in tagonly_and_tuple_field_counts
static void node_tagonly_common_dealloc_{$ count $}(PyObject *obj) {
    node_tagonly_common_dealloc((node_tagonly_common*)obj,{$ count $});
}
static int node_tagonly_common_traverse_{$ count $}(PyObject *obj,visitproc visit,void *arg) {
    return node_tagonly_common_traverse((node_tagonly_common*)obj,visit,arg,{$ count $});
}
//% endfor

typedef struct {
    frozen_list base;
    PyObject *fields[1];
} node_list_common;

static void init_node_list_common(node_list_common *n,size_t fields) {
    size_t i;
    init_frozen_list(&n->base);
    for(i=0; i<fields; ++i) n->fields[i] = NULL;
}
static node_list_common *create_node_list_common(module_state *state,size_t class_index,size_t fields) {
    node_list_common *r = PyObject_GC_New(node_list_common,state->classes[class_index]);
    if(r) init_node_list_common(r,fields);
    PyObject_GC_Track(r);
    return r;
}

static void node_list_common_dealloc(node_list_common *obj,size_t field_count) {
    size_t i;
    size_t size = obj->base.size;
    PyTypeObject *t = Py_TYPE(obj);
    PyObject_GC_UnTrack(obj);
    obj->base.size = 0;
    for(i=0; i<field_count; ++i) Py_CLEAR(obj->fields[i]);
    for(i=0; i<size; ++i) Py_XDECREF(obj->base.content[i]);
    if(obj->base.content) PyMem_Free(obj->base.content);
    ((freefunc)PyType_GetSlot(t,Py_tp_free))(obj);
    Py_DECREF(t);
}
static int node_list_common_traverse(node_list_common *obj,visitproc visit,void *arg,size_t field_count) {
    int r = visit_array(obj->fields,field_count,visit,arg);
    if(r) return r;
    return frozen_list_traverse(&obj->base,visit,arg);
}

//% for count in list_element_field_counts
static void node_list_common_dealloc_{$ count $}(PyObject *obj) {
    node_list_common_dealloc((node_list_common*)obj,{$ count $});
}
static int node_list_common_traverse_{$ count $}(PyObject *obj,visitproc visit,void *arg) {
    return node_list_common_traverse((node_list_common*)obj,visit,arg,{$ count $});
}
//% endfor

/* this steals a reference to "src" */
static int append_str_obj(PyObject **dest,PyObject *src) {
    assert(PyUnicode_CheckExact(*dest));

    PyObject *tmp = PyUnicode_Concat(*dest,src);
    if(tmp == NULL) return -1;
    Py_DECREF(*dest);
    *dest = tmp;
    return 0;
}

static int node_list_common_text(parse_state *state,const XML_Char *str,int size) {
    int r;
    frozen_list *n = &((node_list_common*)*top_callbacks(state)->value)->base;
    PyObject *str_obj = PyUnicode_FromStringAndSize(str,size);
    if(str_obj == NULL) return -1;

    if(n->size && PyUnicode_CheckExact(n->content[n->size-1])) {
        r = append_str_obj(&n->content[n->size-1],str_obj);
        Py_DECREF(str_obj);
        return r;
    }

    r = frozen_list_push_object(n,str_obj,NODE_LIST_INITIAL_CAPACITY);
    if(r) Py_DECREF(str_obj);
    return r;
}

typedef node_tagonly_common tuple_item;
static tuple_item *create_tuple_item(module_state *state,size_t class_index,size_t fields) {
    return create_node_tagonly_common(state,class_index,fields);
}

static PyObject *tuple_item_item_common(PyObject *obj,Py_ssize_t i,Py_ssize_t size) {
    if(i < 0 || i >= size) {
        PyErr_SetString(PyExc_IndexError,"index out of range");
        return NULL;
    }
    PyObject *r = ((tuple_item*)obj)->fields[i];
    Py_INCREF(r);
    return r;
}

//% for count in tuple_field_counts
static Py_ssize_t tuple_item_size_{$ count $}(PyObject *Py_UNUSED(obj)) {
    return {$ count $};
}

static PyObject *tuple_item_item_{$ count $}(PyObject *obj,Py_ssize_t i) {
    return tuple_item_item_common(obj,i,{$ count $});
}
//% endfor

static PyObject **frozen_list_push_tuple_item(parse_state *state,Py_ssize_t tuple_i,Py_ssize_t tuple_size,const char **field_names,int class_index,frozen_list *fl,size_t initial_cap) {
    assert(tuple_size > 0);
    if(tuple_i == 0) {
        tuple_item *new_tuple;

        if(fl->size && ((tuple_item*)fl->content[fl->size-1])->fields[tuple_size-1] == NULL) {
            set_parse_error_format(
                state,
                "\"%s\" element can only come after \"%s\" element or be the first in its group",
                field_names[0],
                field_names[tuple_size-1]);
            return NULL;
        }

        new_tuple = create_tuple_item(state->py,class_index,(size_t)tuple_size);
        if(frozen_list_push_object(fl,(PyObject*)new_tuple,initial_cap)) {
            Py_DECREF(new_tuple);
            return NULL;
        }
        return &new_tuple->fields[0];
    }

    if(!fl->size || ((tuple_item*)fl->content[fl->size-1])->fields[tuple_i-1] == NULL) {
        set_parse_error_format(
            state,
            "\"%s\" element can only come after \"%s\" element",
            field_names[tuple_i],
            field_names[tuple_i-1]);
        return NULL;
    }
    return &((tuple_item*)fl->content[fl->size-1])->fields[tuple_i];
}

static int frozen_list_check_complete_tuple(parse_state *state,Py_ssize_t tuple_size,const char **field_names,frozen_list *fl) {
    if(fl->size) {
        tuple_item *last = (tuple_item*)fl->content[fl->size-1];
        assert(last->fields[0] != NULL);
        Py_ssize_t i = tuple_size;
        while(last->fields[i-1] == NULL) --i;
        if(i != tuple_size) {
            set_parse_error_format(
                state,
                "\"%s\" element must come after \"%s\" element",
                field_names[i],
                field_names[i-1]);
            return -1;
        }
    }
    return 0;
}

{% macro common_affix(type) %}{$ 'list' if type is list_e else 'tagonly' $}{% endmacro %}

//% for type in types
//%   if type is used_directly

enum {
//%     for b,off in type|base_offsets
    BASE_FIELD_OFFSET__{$ type $}{$ '__'~b if b $} = {$ off $}{$ ',' if not loop.last $}
//%     endfor
};

enum {
//%     if type is has_fields
//%       for f in type.fields()
    FIELD__{$ type $}__{$ f.py_name $}{$ ' = BASE_FIELD_OFFSET__'~type if loop.first $},
//%       endfor
    FIELD_COUNT__{$ type $}
//%     else
    FIELD_COUNT__{$ type $} = 0
//%     endif
};

//%     if type is has_fields
//%       if type.direct_field_count
static const py_field_type FIELD_PY_INDICES__{$ type $}[] = {
//%         for f in type.fields()
    PY_FIELD__{$ f.py_name $},
//%         endfor
};
//%       endif
static Py_ssize_t assign_field_name_tuple__{$ type $}(PyObject *dest,PyObject **names,Py_ssize_t start_i);
//%     endif

//%     if type is has_fields
static PyMemberDef node_class_members__{$ type $}[] = {
//%       for f in type.fields()
    {"{$ f.py_name $}",T_OBJECT_EX,offsetof(node_{$ common_affix(type) $}_common,fields) + FIELD__{$ type $}__{$ f.py_name $} * sizeof(PyObject*),READONLY,NULL},
//%       endfor
    {NULL}
};
//%     endif

static PyObject *node_class_tp_new__{$ type $}(PyTypeObject *subtype,PyObject *args,PyObject *kwds);

static PyType_Slot node_class_slots__{$ type $}[] = {
    {Py_tp_new,node_class_tp_new__{$ type $}},
//%     if type is has_fields
    {Py_tp_members,node_class_members__{$ type $}},
//%     endif
    {Py_tp_dealloc,node_{$ common_affix(type) $}_common_dealloc_{$ type|field_count $}},
    {Py_tp_traverse,node_{$ common_affix(type) $}_common_traverse_{$ type|field_count $}},
    {0,NULL}
};


//%     if type is has_children_or_content
static int node_class_child_start__{$ type $}(parse_state*,const XML_Char*,const XML_Char**);
//%     endif
//%     if type is has_children_or_tuple_content
static int node_class_finish__{$ type $}(parse_state*);
//%     endif
static int node_class_start__{$ type $}(parse_state*,PyObject**,const XML_Char**);
//%   endif
//%   if type is element
//%     if type|field_count
static void node_class_new_set_fields__{$ type $}(PyObject **fields,PyObject *args,Py_ssize_t start_i);
static int node_class_new_set_kw_field__{$ type $}(module_state *state,PyObject **fields,py_field_type field,PyObject *value);
static int node_class_new_fields_end__{$ type $}(module_state *state,PyObject **fields);
//%     endif
//%     if type is has_attributes
static int node_class_attr__{$ type $}(parse_state*,{$ 'PyObject**,' if type is has_fields $}attribute_type,const XML_Char**);
static int node_class_attr_end__{$ type $}(parse_state*,PyObject**);
//%     endif
//%     if type is has_children_or_content
static int node_class_child__{$ type $}(parse_state*,{$ 'PyObject**,' if type is has_fields $}element_type,const XML_Char**);
//%     endif
//%     if type is has_children
static int node_class_finish_fields__{$ type $}(parse_state *state,PyObject **fields);
//%     endif
//%   elif type is enumeration_t or type is char_enum_t
static PyObject *parse__{$ type $}(parse_state*,const char*);
//%   endif
//%   if type is content_tuple

enum {
//%       for field,ftype in type|content
    TUPLE_ITEM_FIELD__{$ type $}__{$ field $}{$ ' = 0' if loop.first $},
//%       endfor
    TUPLE_ITEM_FIELD_COUNT__{$ type $}
};

const char *tuple_item_field_names__{$ type $}[] = {
//%       for field,ftype in type|content
    "{$ field $}",
//%       endfor
    NULL /* needed by tp_new method */
};

static PyMemberDef tuple_item_members__{$ type $}[] = {
//%       for field,ftype in type|content
    {"{$ field $}",T_OBJECT_EX,offsetof(tuple_item,fields) + TUPLE_ITEM_FIELD__{$ type $}__{$ field $} * sizeof(PyObject*),READONLY,NULL},
//%       endfor
    {NULL}
};

PyObject *tuple_item_tp_new__{$ type $}(PyTypeObject *subtype,PyObject *args,PyObject *kwds) {
    size_t i;
    tuple_item *r = (tuple_item*)((allocfunc)PyType_GetSlot(subtype,Py_tp_alloc))(subtype,0);
    if(r == NULL) return NULL;

    if(!PyArg_ParseTupleAndKeywords(
            args,
            kwds,
            "{$ 'O'*(type|content|length) $}:{$ type $}.__new__",
            (char**)tuple_item_field_names__{$ type $},
//%     for _ in type|content
            &r->fields[{$ loop.index0 $}]{$ ',' if not loop.last $}
//%     endfor
    )) {
        for(i=0; i<TUPLE_ITEM_FIELD_COUNT__{$ type $}; ++i) r->fields[i] = NULL;
        Py_DECREF(r);
        return NULL;
    }
    for(i=0; i<TUPLE_ITEM_FIELD_COUNT__{$ type $}; ++i) Py_INCREF(r->fields[i]);

    return (PyObject*)r;
}

static PyType_Slot tuple_item_slots__{$ type $}[] = {
    {Py_tp_members,tuple_item_members__{$ type $}},
    {Py_tp_dealloc,node_tagonly_common_dealloc_{$ type.content|length $}},
    {Py_tp_traverse,node_tagonly_common_traverse_{$ type.content|length $}},
    {Py_sq_length,tuple_item_size_{$ type.content|length $}},
    {Py_sq_item,tuple_item_item_{$ type.content|length $}},
    {0,NULL}
};

//%   endif
//% endfor

static int warn_unexpected_attribute(parse_state *state,const char *name) {
    return SET_PARSE_WARNING_FMT(state,"unexpected attribute \"%s\"",name);
}

static int warn_duplicate_attribute(parse_state *state,const char *name) {
    return SET_PARSE_WARNING_FMT(state,"duplicate attribute \"%s\"",name);
}

static void raise_missing_attribute_error(parse_state *state,const char *name) {
    set_parse_error_format(state,"missing \"%s\" attribute",name);
}

static void raise_duplicate_element_error(parse_state *state,const char *name) {
    set_parse_error_format(state,"\"%s\" cannot appear more than once in this context",name);
}

static void raise_missing_element_error(parse_state *state,const char *name) {
    set_parse_error_format(state,"missing \"%s\" child",name);
}
static void raise_empty_list_element_error(parse_state *state,const char *name) {
    set_parse_error_format(state,"at least one \"%s\" child is required",name);
}

static void raise_invalid_enum_error(parse_state *state,const char *value) {
    set_parse_error_format(state,"\"%s\" is not one of the allowed enumeration values",value);
}

static void raise_invalid_char_enum_error(parse_state *state,char c,const char *allowed) {
    set_parse_error_format(state,"\"%c\" is not one of the allowed character values; must be one of \"%s\"",c,allowed);
}

int parse_integer(parse_state *state,const char *str,long *value) {
    char *end;
    errno = 0;
    *value = strtol(str,&end,10);
    if(errno != 0 || non_whitespace(end,-1)) {
        errno = 0;
        set_parse_error(state,"cannot parse integer");
        return -1;
    }
    return 0;
}

static int set_string_attribute(parse_state *state,PyObject **field,const XML_Char **attr) {
    PyObject *tmp;
    if(*field != NULL) return warn_duplicate_attribute(state,attr[0]);
    tmp = PyUnicode_FromString(attr[1]);
    if(tmp == NULL) return -1;
    *field = tmp;
    return 0;
}
static int set_integer_attribute(parse_state *state,PyObject **field,const XML_Char **attr) {
    long value;
    PyObject *tmp;
    if(*field != NULL) return warn_duplicate_attribute(state,attr[0]);

    if(parse_integer(state,attr[1],&value)) return -1;
    tmp = PyLong_FromLong(value);
    if(tmp == NULL) return -1;
    *field = tmp;
    return 0;
}
static int set_DoxBool_attribute(parse_state *state,PyObject **field,const XML_Char **attr) {
    if(*field != NULL) return warn_duplicate_attribute(state,attr[0]);
    if(strcmp(attr[1],"yes") == 0) *field = Py_True;
    else if(strcmp(attr[1],"no") == 0) *field = Py_False;
    else {
        set_parse_error_format(state,"\"%s\" must be \"yes\" or \"no\"",attr[0]);
        return -1;
    }
    Py_INCREF(*field);
    return 0;
}

static int node_string_text(parse_state *state,const XML_Char *str,int size) {
    PyObject **dest = top_callbacks(state)->value;
    PyObject *tmp = PyUnicode_FromStringAndSize(str,size);
    if(tmp == NULL) return -1;
    if(*dest == NULL) *dest = tmp;
    else {
        PyObject *joined = PyUnicode_Concat(*dest,tmp);
        if(joined == NULL) {
            Py_DECREF(tmp);
            return -1;
        }
        Py_DECREF(*dest);
        *dest = joined;
        Py_DECREF(tmp);
    }
    return 0;
}
static int node_string_end(parse_state *state) {
    PyObject **dest = top_callbacks(state)->value;
    if(*dest == NULL) {
        *dest = PyUnicode_FromStringAndSize(NULL,0);
        if(*dest == NULL) return -1;
    }
    return 0;
}
static int node_start_string(parse_state *state,PyObject **dest,const XML_Char **attr) {
    parse_callbacks *cb;

    for(; *attr != NULL; attr += 2) {
        if(warn_unexpected_attribute(state,attr[0])) return -1;
    }

    *dest = NULL;

    cb = push_callbacks(state);
    if(cb == NULL) return -1;

    cb->value = dest;
    cb->cs_call = NULL;
    cb->f_call = node_string_end;
    cb->t_call = node_string_text;

    return 1;
}

static int node_start_empty(parse_state *state,PyObject **dest,const XML_Char **attr) {
    parse_callbacks *cb;

    for(; *attr != NULL; attr += 2) {
        if(warn_unexpected_attribute(state,attr[0])) return -1;
    }

    cb = push_callbacks(state);
    if(cb == NULL) return -1;

    *dest = Py_None;
    Py_INCREF(Py_None);

    cb->value = NULL;
    cb->cs_call = NULL;
    cb->f_call = NULL;
    cb->t_call = NULL;

    return 1;
}

static int node_start_spType(parse_state *state,PyObject **dest,const XML_Char **attr) {
    parse_callbacks *cb;
    PyObject *c_obj;
    char c = ' ';

    for(; *attr != NULL; attr += 2) {
        long value;

        if(strcmp(attr[0],"value") != 0) {
            if(warn_unexpected_attribute(state,attr[0])) return -1;
        }

        if(parse_integer(state,attr[1],&value)) return -1;
        if(value < 0 || value > 127) {
            set_parse_error(state,"\"value\" must be between 0 and 127");
            return -1;
        }
        c = (char)value;
    }

    c_obj = PyUnicode_FromStringAndSize(&c,1);
    if(c_obj == NULL) return -1;
    if(*dest == NULL) *dest = c_obj;
    else {
        int r = append_str_obj(dest,c_obj);
        Py_DECREF(c_obj);
        if(r) return -1;
    }

    cb = push_callbacks(state);
    if(cb == NULL) return -1;

    cb->value = NULL;
    cb->cs_call = NULL;
    cb->f_call = NULL;
    cb->t_call = NULL;

    return 1;
}

static void raise_dup_field_error(const char *name) {
    PyErr_Format(PyExc_TypeError,"received more than one value for \"%s\"",name);
}

static void raise_too_many_args_count(const char *func,Py_ssize_t given,Py_ssize_t maximum) {
    PyErr_Format(PyExc_TypeError,"%s takes at most %zi arguments, %zi were given",func,maximum,given);
}
static void raise_invalid_keyword_arg(const char *func,PyObject *key) {
    PyErr_Format(PyExc_TypeError,"%s does not take the keyword argument \"%U\"",func,key);
}
static void raise_needs_value_arg(const char *func,const char *key) {
    PyErr_Format(PyExc_TypeError,"%s called with missing argument: \"%s\"",func,key);
}
static void raise_needs_pos_arg(const char *func,Py_ssize_t i) {
    PyErr_Format(PyExc_TypeError,"%s called with missing positional argument #%zi",func,i+1);
}

static int node_set_py_field(module_state *state,PyObject **field,PyObject *value,const char *name) {
    if(*field != NULL) {
        raise_dup_field_error(name);
        return -1;
    }

    *field = value;
    Py_INCREF(value);
    return 1;
}

static int node_set_py_field_frozen_list(module_state *state,PyObject **field,PyObject *value,const char *name) {
    if(*field != NULL) {
        raise_dup_field_error(name);
        return -1;
    }

    if(PyObject_TypeCheck(value,state->classes[CLASS_FROZEN_LIST])) {
        *field = value;
        return 1;
    }

    *field = (PyObject*)create_frozen_list(state);
    if(*field == NULL) return -1;
    if(frozen_list_fill((frozen_list*)*field,value)) return -1;
    return 1;
}

//% macro handle_attr(name,field,type)
//%   if type is builtin_t
        if(set_{$ type $}_attribute(state,&fields[FIELD__{$ name $}__{$ field $}],attr)) return -1;
//%   else
        if(fields[FIELD__{$ name $}__{$ field $}] != NULL) {
            if(warn_duplicate_attribute(state,"{$ field $}")) return -1;
        } else {
            fields[FIELD__{$ name $}__{$ field $}] = parse__{$ type $}(state,attr[1]);
            if(fields[FIELD__{$ name $}__{$ field $}] == NULL) return -1;
        }
//%   endif
//% endmacro

//% for type in types
//%   if type is element
//%     if type|field_count
static Py_ssize_t assign_field_name_tuple__{$ type $}(PyObject *dest,PyObject **names,Py_ssize_t start_i) {
//%       if type.direct_field_count
    int i;
//%       endif
//%       for b in type.bases if b|field_count
    start_i = assign_field_name_tuple__{$ b $}(dest,start_i);
//%       endfor
//%       if type.direct_field_count
    for(i = 0; i < FIELD_COUNT__{$ type $} - BASE_FIELD_OFFSET__{$ type $}; ++i, ++start_i) {
        PyObject *name = names[FIELD_PY_INDICES__{$ type $}[i]];
        PyTuple_SetItem(dest,start_i,name);
        Py_INCREF(name);
    }
//%       endif
    return start_i;
}

static void node_class_new_set_fields__{$ type $}(PyObject **fields,PyObject *args,Py_ssize_t start_i) {
//%       for b in type.bases if b|field_count
    node_class_new_set_fields__{$ b $}(fields + BASE_FIELD_OFFSET__{$ type $}__{$ b $},args,start_i + BASE_FIELD_OFFSET__{$ type $}__{$ b $});
//%       endfor
    if(PyTuple_Size(args) - start_i > 0) {
        switch(PyTuple_Size(args) - start_i) {
//%       for f in type.fields()|reverse
        {$ 'default' if loop.first else 'case '~loop.revindex $}:
            fields[FIELD__{$ type $}__{$ f.py_name $}] = PyTuple_GetItem(args,FIELD__{$ type $}__{$ f.py_name $} + start_i);
            assert(fields[FIELD__{$ type $}__{$ f.py_name $}]);
            Py_INCREF(fields[FIELD__{$ type $}__{$ f.py_name $}]);
//%       endfor
        }
    }
}
static int node_class_new_set_kw_field__{$ type $}(module_state *state,PyObject **fields,py_field_type field,PyObject *value) {
//%       for b in type.bases if b|field_count
//%         if loop.first
    int r;
//%         endif
    r = node_class_new_set_kw_field__{$ b $}(fields + BASE_FIELD_OFFSET__{$ type $}__{$ b $},args,start_i + BASE_FIELD_OFFSET__{$ type $}__{$ b $});
    if(r) return r;
//%       endfor
    switch(field) {
//%       for ref in type|attributes
    case PY_FIELD__{$ ref.py_name $}:
        return node_set_py_field(state,&fields[FIELD__{$ type $}__{$ ref.py_name $}],value,"{$ ref.py_name $}");
//%       endfor
//%       for ref in type|children
    case PY_FIELD__{$ ref.py_name $}:
        return node_set_py_field{$ '_frozen_list' if ref.is_list $}(state,&fields[FIELD__{$ type $}__{$ ref.py_name $}],value,"{$ ref.py_name $}");
//%       endfor
    default:
        return 0;
    }
}
static int node_class_new_fields_end__{$ type $}(module_state *state,PyObject **fields) {
//%     for b in type.bases if b|field_count
    if(node_class_new_fields_end__{$ b $}(fields + BASE_FIELD_OFFSET__{$ type $}__{$ b $})) return -1;
//%     endfor
//%     for ref in type|attributes
    if(fields[FIELD__{$ type $}__{$ ref.py_name $}] == NULL) {
//%       if ref.optional
        fields[FIELD__{$ type $}__{$ ref.py_name $}] = Py_None;
        Py_INCREF(Py_None);
//%       else
        raise_needs_value_arg("Node_{$ type $}.__new__","{$ ref.py_name $}");
        return -1;
//%       endif
    }
//%     endfor
//%     for ref in type|children
    if(fields[FIELD__{$ type $}__{$ ref.py_name $}] == NULL) {
//%       if ref.is_list
        fields[FIELD__{$ type $}__{$ ref.py_name $}] = (PyObject*)create_frozen_list(state);
        if(fields[FIELD__{$ type $}__{$ ref.py_name $}] == NULL) return -1;
//%       elif ref.min_items == 0
        fields[FIELD__{$ type $}__{$ ref.py_name $}] = Py_None;
        Py_INCREF(Py_None);
//%       else
        raise_needs_value_arg("Node_{$ type $}.__new__","{$ ref.py_name $}");
        return -1;
//%       endif
    }
//%     endfor
    return 0;
}

//%     endif
//%     if type is has_attributes
static int node_class_attr__{$ type $}(parse_state *state,{$ 'PyObject **fields,' if type is has_fields $}attribute_type attr_index,const XML_Char **attr) {
//%       for b in type.bases|select('has_attributes')
//%         if loop.first
    int r;
//%         endif
    r = node_class_attr__{$ b $}(state,{$ 'fields+BASE_FIELD_OFFSET__'~type~__~b~',' if type is has_fields and b is has_fields $}attr_index,attr);
    if(r != 0) return r;
//%       endfor
    switch(attr_index) {
//%       for attr in type|attributes
    case ATTRIBUTE__{$ attr.name $}:
{$ handle_attr(type,attr.py_name,attr.type) $}        return 1;
//%       endfor
    default:
        return 0;
    }
}
static int node_class_attr_end__{$ type $}(parse_state *state, PyObject **fields) {
//%       for b in type.bases if b is has_attributes
    if(node_class_attr_end__{$ b $}(state),fields + BASE_FIELD_OFFSET__{$ type $}__{$ b $}) return -1;
//%       endfor
//%       for ref in type|attributes
    if(fields[FIELD__{$ type $}__{$ ref.py_name $}] == NULL) {
//%         if ref.optional
        fields[FIELD__{$ type $}__{$ ref.py_name $}] = Py_None;
        Py_INCREF(Py_None);
//%         else
        raise_missing_attribute_error(state,"{$ ref.name $}");
        return -1;
//%         endif
    }
//%       endfor
    return 0;
}
//%     endif
//%     if type is has_children_or_content
static int node_class_child__{$ type $}(parse_state *state,{$ 'PyObject **fields,' if type is has_fields $}element_type e_index,const XML_Char **attr) {
//%       if type is list_e and type.content
    node_list_common *n;
//%       endif
//%       if type.bases
    int r;
//%         for b in type.bases|select('has_children_or_content')
    r = node_class_child__{$ b $}(state,{$ 'fields+BASE_FIELD_OFFSET__'~type~'__'~b~',' if type is has_fields and b is has_fields $}e_index,attr);
    if(r) return r;
//%         endfor
//%       endif
//%       if type is list_e and type.content
    n = (node_list_common*)*top_callbacks(state)->value;
//%       endif
    switch(e_index) {
//%       for cref in type|children
    case ELEMENT__{$ cref.name $}:
//%         if cref.is_list
        assert(fields[FIELD__{$ type $}__{$ cref.py_name $}] != NULL && Py_TYPE(fields[FIELD__{$ type $}__{$ cref.py_name $}]) == state->py->classes[CLASS_FROZEN_LIST]);
        {
            frozen_list *fl = (frozen_list*)fields[FIELD__{$ type $}__{$ cref.py_name $}];
            if(frozen_list_push_object(fl,NULL,NODE_LIST_INITIAL_CAPACITY)) return -1;
            return node_{$ 'start_' if cref.type is builtin_t else 'class_start__' $}{$ cref.type $}(state,fl->content + (fl->size-1),attr);
        }
//%         else
        if(fields[FIELD__{$ type $}__{$ cref.py_name $}] != NULL) {
            raise_duplicate_element_error(state,"{$ cref.name $}");
            return -1;
        }
        return node_{$ 'start_' if cref.type is builtin_t else 'class_start__' $}{$ cref.type $}(state,&fields[FIELD__{$ type $}__{$ cref.py_name $}],attr);
//%         endif
//%       endfor
//%       for cname,ctype in type|content
    case ELEMENT__{$ cname $}:
//%         if type is content_tuple
        {
            PyObject **dest = frozen_list_push_tuple_item(
                state,
                TUPLE_ITEM_FIELD__{$ type $}__{$ cname $},
                TUPLE_ITEM_FIELD_COUNT__{$ type $},
                tuple_item_field_names__{$ type $},
                CLASS_ITEM__{$ type $},
                &n->base,
                NODE_LIST_INITIAL_CAPACITY);
            if(dest == NULL) return -1;
            return node_{$ 'start_' if ctype is builtin_t else 'class_start__' $}{$ ctype $}(state,dest,attr);
        }
//%         elif type is content_union
        {
//%           if ctype is appends_str
            PyObject **dest;
            if(n->base.size && PyUnicode_CheckExact(n->base.content[n->base.size-1])) {
                dest = &n->base.content[n->base.size-1];
            } else {
                dest = frozen_list_push_tagged_value(state->py,TAGGED_UNION_NAME__{$ cname $},&n->base,NODE_LIST_INITIAL_CAPACITY);
                if(dest == NULL) return -1;
            }
//%           else
            PyObject **dest = frozen_list_push_tagged_value(state->py,TAGGED_UNION_NAME__{$ cname $},&n->base,NODE_LIST_INITIAL_CAPACITY);
            if(dest == NULL) return -1;
//%           endif
            return node_{$ 'start_' if ctype is builtin_t else 'class_start__' $}{$ ctype $}(state,dest,attr);
        }
//%         else
        if(frozen_list_push_object(&n->base,NULL,NODE_LIST_INITIAL_CAPACITY)) return -1;
        return node_{$ 'start_' if ctype is builtin_t else 'class_start__' $}{$ ctype $}(state,n->base.content + (n->base.size-1),attr);
//%         endif
//%       endfor
    default:
        return 0;
    }
}
//%     endif
//%     if type is used_directly
static PyObject *node_class_tp_new__{$ type $}(PyTypeObject *subtype,PyObject *args,PyObject *kwds) {
    static const char *func_name = "Node_{$ type $}.__new__";
//%       if type|field_count
    PyObject *module;
    module_state *state;
//%       endif
    node_{$ common_affix(type) $}_common *n;
//%       if type|field_count
    PyObject *key, *value;
    Py_ssize_t pos = 0;
//%       endif
    Py_ssize_t p_arg_count = PyTuple_Size(args);
    Py_ssize_t kw_arg_count = kwds == NULL ? 0 : PyDict_Size(kwds);

    if(p_arg_count+kw_arg_count > FIELD_COUNT__{$ type $}{$ ' + 1' if type is list_e $}) {
        raise_too_many_args_count(func_name,p_arg_count+kw_arg_count,FIELD_COUNT__{$ type $}{$ ' + 1' if type is list_e $});
        return NULL;
    }

//%       if type is list_e
    if(p_arg_count < 1) {
        raise_needs_pos_arg(func_name,0);
        return NULL;
    }
//%       endif

//%       if type|field_count
    module = PyState_FindModule(&module_def);
    assert(module);
    state = PyModule_GetState(module);
//%       endif
    
    n = (node_{$ common_affix(type) $}_common*)((allocfunc)PyType_GetSlot(subtype,Py_tp_alloc))(subtype,0);
    if(n == NULL) return NULL;
    init_node_{$ common_affix(type) $}_common(n,FIELD_COUNT__{$ type $});

//%       if type is list_e
    if(frozen_list_fill(&n->base,PyTuple_GetItem(args,0))) {
        Py_DECREF(n);
        return NULL;
    }
//%       endif

//%       if type|field_count
    node_class_new_set_fields__{$ type $}(n->fields,args,{$ '1' if type is list_e else '0' $});

    if(kw_arg_count) {
        while(PyDict_Next(kwds,&pos,&key,&value)) {
            int r = node_class_new_set_kw_field__{$ type $}(state,n->fields,py_field_lookup(key),value);
            if(r < 0) {
                Py_DECREF(n);
                return NULL;
            }
            if(!r) {
                raise_invalid_keyword_arg(func_name,key);
                Py_DECREF(n);
                return NULL;
            }
        }
    }

    if(node_class_new_fields_end__{$ type $}(state,n->fields)) {
        Py_DECREF(n);
        return NULL;
    }
//%       endif

    return (PyObject*)n;
}

static int node_class_start__{$ type $}(parse_state *state,PyObject **dest,const XML_Char **attr) {
    parse_callbacks *cb;

    node_{$ common_affix(type) $}_common *n = create_node_{$ common_affix(type) $}_common(state->py,CLASS__{$ type $},FIELD_COUNT__{$ type $});
    if(n == NULL) return -1;
    *dest = (PyObject*)n;

//%       for ref in type|children
//%         if ref.is_list
    n->fields[FIELD__{$ type $}__{$ ref.py_name $}] = (PyObject*)create_frozen_list(state->py);
    if(n->fields[FIELD__{$ type $}__{$ ref.py_name $}] == NULL) return -1;
//%         endif
//%       endfor

//%       if type is has_attributes or type.other_attr == OtherAttrAction.error
    for(; *attr != NULL; attr += 2) {
//%         if type is has_attributes
        int r;
        attribute_type attr_index = attribute_lookup(attr[0]);
        r = node_class_attr__{$ type $}(state,n->fields,attr_index,attr);
//%           if type.other_attr == OtherAttrAction.error
        if(r < 0 || (r == 0 && warn_unexpected_attribute(state,attr[0]))) return -1;
//%           else
        if(r < 0) return -1;
//%           endif
//%         else
        if(warn_unexpected_attribute(state,attr[0])) return -1;
//%         endif
    }
//%       endif
//%       if type is has_attributes
    if(node_class_attr_end__{$ type $}(state,n->fields)) return -1;
//%       endif

    cb = push_callbacks(state);
    if(cb == NULL) return -1;

    cb->value = dest;

//%       if type is has_children_or_content
    cb->cs_call = node_class_child_start__{$ type $};
//%       else
    cb->cs_call = NULL;
//%       endif
//%       if type is has_children_or_tuple_content
    cb->f_call = node_class_finish__{$ type $};
//%       else
    cb->f_call = NULL;
//%       endif
//%       if type is allow_text
    cb->t_call = node_list_common_text;
//%       else
    cb->t_call = NULL;
//%       endif

    return 1;
}
//%       if type is has_children_or_content
static int node_class_child_start__{$ type $}(parse_state *state,const XML_Char *child_name,const XML_Char **attr) {
    assert(Py_TYPE(*top_callbacks(state)->value) == state->py->classes[CLASS__{$ type $}]);
//%         if type is has_fields
    node_{$ common_affix(type) $}_common *n = (node_{$ common_affix(type) $}_common*)*top_callbacks(state)->value;
//%         endif
    return node_class_child__{$ type $}(state,{$ 'n->fields,' if type is has_fields $}element_lookup(child_name),attr);
}
//%       endif
//%       if type is has_children
static int node_class_finish_fields__{$ type $}(parse_state *state,PyObject **fields) {
//%         for b in type.bases|select('has_children')
    if(node_class_finish_fields__{$ b $}(state,fields+BASE_FIELD_OFFSET__{$ type $}__{$ b $})) return -1;
//%         endfor
//%         for ref in type|children
//%           if ref.min_items
//%             if ref.is_list
    assert(fields[FIELD__{$ type $}__{$ ref.py_name $}] != NULL && Py_TYPE(fields[FIELD__{$ type $}__{$ ref.py_name $}]) == state->py->classes[CLASS_FROZEN_LIST]);
    if(((frozen_list*)fields[FIELD__{$ type $}__{$ ref.py_name $}])->size < 1) {
        raise_empty_list_element_error(state,"{$ ref.name $}");
        return -1;
    }
//%             else
    if(fields[FIELD__{$ type $}__{$ ref.py_name $}] == NULL) {
        raise_missing_element_error(state,"{$ ref.name $}");
        return -1;
    }
//%             endif
//%           elif not ref.is_list
    if(fields[FIELD__{$ type $}__{$ ref.py_name $}] == NULL) {
        fields[FIELD__{$ type $}__{$ ref.py_name $}] = Py_None;
        Py_INCREF(Py_None);
    }
//%           endif
//%         endfor
    return 0;
}
//%       endif
//%       if type is has_children_or_tuple_content
static int node_class_finish__{$ type $}(parse_state *state) {
    assert(Py_TYPE(*top_callbacks(state)->value) == state->py->classes[CLASS__{$ type $}]);
    node_{$ common_affix(type) $}_common *n = (node_{$ common_affix(type) $}_common*)*top_callbacks(state)->value;
//%         if type is has_children
    if(node_class_finish_fields__{$ type $}(state,n->fields)) return -1;
//%         endif
//%         if type is content_tuple and type.content|length > 1
    return frozen_list_check_complete_tuple(state,TUPLE_ITEM_FIELD_COUNT__{$ type $},tuple_item_field_names__{$ type $},&n->base);
//%         else
    return 0;
//%         endif
}
//%       endif
//%     endif
//%   elif type is enumeration_t
static const char *enum_value_str__{$ type $}[] = {
//%     for value in type.children
    "{$ value.xml $}"{$ ',' if not loop.last $}
//%     endfor
};
//%     if type.any_renamed
static const char *enum_id_str__{$ type $}[] = {
//%       for value in type.children
    "{$ value.id $}"{$ ',' if not loop.last $}
//%       endfor
};
//%     endif 

{$ hash_lookup('enum_value_lookup__'~type,type.hash,'ENUM_VALUE_COUNT__'~type,'enum_value_str__'~type,'int',-1) $}

static PyObject *parse__{$ type $}(parse_state *state,const char *str) {
    /* TODO: ignore whitespace */
    PyObject *r;
    int i = enum_value_lookup__{$ type $}(str);

    if(i < 0) {
        raise_invalid_enum_error(state,str);
        return NULL;
    }
    r = state->py->enum_values__{$ type $}[i];
    Py_INCREF(r);
    return r;
}
//%   elif type is char_enum_t
static PyObject *parse__{$ type $}(parse_state *state,const char *str) {
    /* TODO: ignore whitespace */
    PyObject *r;
    if(str[0] == '\0' || str[1] != '\0') {
        set_parse_error(state,"value must be a single character");
        return NULL;
    }
    switch(*str) {
//%     for c in type.values
    case '{$ c $}':
        r = state->py->char_objects[ENUM_CHAR__{$ c $}];
        break;
//%     endfor
    default:
        raise_invalid_char_enum_error(state,*str,"{$ type.values $}");
        return NULL;
    }
    Py_INCREF(r);
    return r;
}
//%   endif
//% endfor

static int toplevel_start(parse_state *state,const XML_Char *child_name,const XML_Char **attr) {
    tagged_value *tv;
    parse_callbacks *cb = top_callbacks(state);

    switch(element_lookup(child_name)) {
//% for name,type in root_elements
    case ELEMENT__{$ name $}:
        if(*cb->value != NULL) set_parse_error(state,"cannot have more than one root element");
        tv = create_tagged_value(state->py);
        if(tv == NULL) return -1;
        *cb->value = (PyObject*)tv;
        tv->values[0] = state->py->tag_names[TAGGED_UNION_NAME__{$ name $}];
        Py_INCREF(tv->values[0]);
        return node_{$ 'start_' if ctype is builtin_t else 'class_start__' $}{$ type $}(state,tv->values + 1,attr);
//% endfor
    default:
        return 0;
    }
}

typedef enum {
    CLASS_TYPE_OTHER,
    CLASS_TYPE_NODE_SUB,
    CLASS_TYPE_LIST_NODE_SUB
} class_type;

typedef struct {
    PyType_Spec spec;
    Py_ssize_t field_count;
    Py_ssize_t (*assign_field_name_tuple)(PyObject*,PyObject**,Py_ssize_t);
    class_type type;
} spec_details;

static PyType_Slot empty_slots[1] = {{0,NULL}};

static spec_details class_specs[] = {
    {{FULL_MODULE_STR ".Node",0,0,Py_TPFLAGS_DEFAULT|Py_TPFLAGS_BASETYPE,empty_slots},0,NULL,CLASS_TYPE_OTHER},
    {{FULL_MODULE_STR ".FrozenList",sizeof(frozen_list),0,Py_TPFLAGS_DEFAULT|Py_TPFLAGS_BASETYPE|Py_TPFLAGS_HAVE_GC|Py_TPFLAGS_SEQUENCE,frozen_list_slots},0,NULL,CLASS_TYPE_OTHER},
    {{FULL_MODULE_STR ".FrozenListItr",sizeof(frozen_list_itr),0,Py_TPFLAGS_DEFAULT|Py_TPFLAGS_HAVE_GC,frozen_list_itr_slots},0,NULL,CLASS_TYPE_OTHER},
    {{FULL_MODULE_STR ".TaggedValue",sizeof(tagged_value),0,Py_TPFLAGS_DEFAULT|Py_TPFLAGS_HAVE_GC|Py_TPFLAGS_SEQUENCE,tagged_value_slots},0,NULL,CLASS_TYPE_OTHER},
    {{FULL_MODULE_STR ".ParseError",0,0,Py_TPFLAGS_DEFAULT,parse_error_slots},0,NULL,CLASS_TYPE_OTHER},
    {{FULL_MODULE_STR ".ParseWarning",0,0,Py_TPFLAGS_DEFAULT,parse_warning_slots},0,NULL,CLASS_TYPE_OTHER},
//% for type in types|select('used_directly')
    {{FULL_MODULE_STR ".Node_{$ type $}",offsetof(node_{$ common_affix(type) $}_common,fields){%
        if type is has_fields %} + sizeof(PyObject*)*FIELD_COUNT__{$ type $}{% endif
        %},0,Py_TPFLAGS_DEFAULT|Py_TPFLAGS_HAVE_GC,node_class_slots__{$ type $}},FIELD_COUNT__{$ type $},{$
        'assign_field_name_tuple__'~type if type is has_fields else 'NULL'
        $},CLASS_TYPE_{$ 'LIST_' if type is list_e $}NODE_SUB},
//% endfor
//% for type in types|select('content_tuple')
    {{FULL_MODULE_STR ".ListItem_{$ type $}",offsetof(tuple_item,fields) + sizeof(PyObject*)*TUPLE_ITEM_FIELD_COUNT__{$ type $},0,Py_TPFLAGS_DEFAULT|Py_TPFLAGS_HAVE_GC,tuple_item_slots__{$ type $}},0},
//% endfor
};

static void raise_expat_error(parse_state *state) {
    if(!PyErr_Occurred()) {
        PyObject *exc = PyObject_CallFunction(
            (PyObject*)state->py->classes[CLASS_PARSE_ERROR],
            "sl",
            XML_ErrorString(XML_GetErrorCode(state->parser)),
            (long)XML_GetErrorLineNumber(state->parser));
        if(exc == NULL) return;
        PyErr_SetObject((PyObject*)state->py->classes[CLASS_PARSE_ERROR],exc);
        Py_DECREF(exc);
    }
}

static int call_read(void *buf,int buf_size,PyObject *read,PyObject *buf_arg) {
    PyObject *str;
    Py_ssize_t len;
    const char *ptr;

    str = PyObject_CallObject(read,buf_arg);
    if(str == NULL) return -1;

    if(PyBytes_Check(str)) {
        char *tmp;
        if(PyBytes_AsStringAndSize(str,&tmp,&len) < 0) goto error;
        ptr = tmp;
    } else if(PyByteArray_Check(str)) {
        ptr = PyByteArray_AsString(str);
        assert(ptr);
        len = PyByteArray_Size(str);
    } else {
        PyErr_SetString(PyExc_TypeError,"read() did not return a bytes object");
        goto error;
    }

    if(len > buf_size) {
        PyErr_Format(
            PyExc_ValueError,
            "read() returned too much data: %i bytes requested, %zd returned",
            buf_size,
            len);
        goto error;
    }
    memcpy(buf,ptr,len);
    Py_DECREF(str);
    return (int)len;

error:
    Py_DECREF(str);
    return -1;
}

static int parse_file(parse_state *state,PyObject *file) {
    enum XML_Status status;
    int r = -1;
    int bytes_read;
    void *buf;
    PyObject *read = NULL;
    PyObject *buf_arg;
    PyObject *tmp;

    buf_arg = PyTuple_New(1);
    if(buf_arg == NULL) return -1;

    tmp = PyLong_FromLong(EXPAT_BUFFER_SIZE);
    if(tmp == NULL) goto end;
    PyTuple_SetItem(buf_arg,0,tmp);

    read = PyObject_GetAttrString(file,"read");
    if(read == NULL) goto end;

    do {
        buf = XML_GetBuffer(state->parser,EXPAT_BUFFER_SIZE);
        if(buf == NULL) {
            raise_expat_error(state);
            goto end;
        }

        bytes_read = call_read(buf,EXPAT_BUFFER_SIZE,read,buf_arg);
        if(bytes_read < 0) goto end;
        status = XML_ParseBuffer(state->parser,bytes_read,bytes_read == 0);
        if(status == XML_STATUS_ERROR) {
            raise_expat_error(state);
            goto end;
        }
    } while(bytes_read);

    r = 0;

  end:
    Py_XDECREF(read);
    Py_DECREF(buf_arg);
    return r;
}

static int parse_str(parse_state *state,PyObject *data) {
    int r = -1;
    const char *s;
    Py_ssize_t len;
    PyObject *tmp = NULL;

    if(PyUnicode_Check(data)) {
        char *s_tmp;
        tmp = PyUnicode_AsUTF8String(data);
        if(tmp == NULL) return -1;
        if(PyBytes_AsStringAndSize(tmp,&s_tmp,&len) < 0) goto end;
        s = s_tmp;
        XML_SetEncoding(state->parser,"utf-8");
    } else if(PyBytes_Check(data)) {
        char *s_tmp;
        if(PyBytes_AsStringAndSize(data,&s_tmp,&len) < 0) return -1;
        s = s_tmp;
    } else if(PyByteArray_Check(data)) {
        s = PyByteArray_AsString(data);
        assert(s);
        len = PyByteArray_Size(data);
    } else {
        PyErr_SetString(PyExc_TypeError,"argument to \"parse_str\" must be an instance of str, bytes or bytearray");
        return -1;
    }

    while(len > EXPAT_CHUNK_SIZE) {
        if(XML_Parse(state->parser,s,EXPAT_CHUNK_SIZE,0) == XML_STATUS_ERROR) {
            raise_expat_error(state);
            goto end;
        }

        s += EXPAT_CHUNK_SIZE;
        len -= EXPAT_CHUNK_SIZE;
    }

    if(XML_Parse(state->parser,s,(int)len,1) == XML_STATUS_ERROR) {
        raise_expat_error(state);
        goto end;
    }

    r = 0;

  end:
    Py_XDECREF(tmp);
    return r;
}

typedef enum {
    EXPAT_SOURCE_STR,
    EXPAT_SOURCE_FILE
} expat_source;

static PyObject *parse(PyObject *module,expat_source source,PyObject *obj) {
    callback_stack_block *next, *tmp;
    parse_callbacks *cb;
    int r = -1;
    PyObject *r_obj = NULL;
    parse_state state = {NULL};

    state.block = PyMem_Malloc(sizeof(callback_stack_block));
    if(state.block == NULL) {
        PyErr_NoMemory();
        return NULL;
    }
    state.block->prev = NULL;
    state.block->next = NULL;
    state.py = PyModule_GetState(module);
    assert(state.py != NULL);

    cb = push_callbacks(&state);
    if(!cb) goto end;
    cb->value = &r_obj;
    cb->cs_call = toplevel_start;

    state.parser = XML_ParserCreate(NULL);
    XML_SetElementHandler(state.parser,start_element,end_element);
    XML_SetCharacterDataHandler(state.parser,character_data);
    XML_SetUserData(state.parser,&state);

    if(source == EXPAT_SOURCE_STR) r = parse_str(&state,obj);
    else r = parse_file(&state,obj);

    XML_ParserFree(state.parser);

  end:
    while(state.block->prev != NULL) state.block = state.block->prev;
    next = state.block;
    while(next) {
        tmp = next;
        next = next->next;
        PyMem_Free(tmp);
    }

    if(r) {
        Py_XDECREF(r_obj);
        return NULL;
    }

    if(r_obj == NULL) {
        PyObject *exc = PyObject_CallFunction(
            (PyObject*)state.py->classes[CLASS_PARSE_ERROR],
            "sO",
            "document without a recognized root element",
            Py_None);
        if(exc == NULL) return NULL;
        PyErr_SetObject((PyObject*)state.py->classes[CLASS_PARSE_ERROR],exc);
        Py_DECREF(exc);
    }

    return r_obj;
}

static PyObject *impl_parse_str(PyObject *module,PyObject *str) {
    return parse(module,EXPAT_SOURCE_STR,str);
}
static PyObject *impl_parse_file(PyObject *module,PyObject *file) {
    return parse(module,EXPAT_SOURCE_FILE,file);
}

static PyMethodDef module_functions[] = {
    {"parse_str",impl_parse_str,METH_O,NULL},
    {"parse_file",impl_parse_file,METH_O,NULL},
    {NULL}};

static PyObject *load_class_from_mod(const char *cls_name,const char *mod_name) {
    PyObject *cls;
    PyObject *m = PyImport_ImportModule(mod_name);
    if(m == NULL) return NULL;
    cls = PyObject_GetAttrString(m,cls_name);
    Py_DECREF(m);
    return cls;
}

static void decref_array(PyObject **objs,size_t size) {
    while(size-- > 0) Py_DECREF(objs[size]);
}

static int create_enum(
    PyObject **dest,
    PyObject *module,
    PyObject *base,
    const char *enum_name,
    const char **py_names,
    const char **xml_names,
    size_t size)
{
    PyObject **str_objs;
    PyObject *members = NULL;
    PyObject *e = NULL;
    size_t str_i=0;
    size_t e_i=0;
    size_t i;
    int r = -1;

    str_objs = PyMem_Malloc(sizeof(PyObject*) * size);
    if(str_objs == NULL) {
        PyErr_NoMemory();
        return -1;
    }
    for(; str_i<size; ++str_i) {
        str_objs[str_i] = PyUnicode_FromString(py_names[str_i]);
        if(str_objs[str_i] == NULL) goto end;
    }
    members = PyDict_New();
    if(members == NULL) goto end;

    for(i=0; i<size; ++i) {
        int tmp;
        PyObject *val;
        if(py_names[i] == xml_names[i]) {
            val = str_objs[i];
            Py_INCREF(val);
        } else {
            val = PyUnicode_FromString(xml_names[i]);
            if(val == NULL) goto end;
        }
        tmp = PyDict_SetItem(members,str_objs[i],val);
        Py_DECREF(val);
        if(tmp < 0) goto end;
    }
    e = PyObject_CallFunction(base,"sO",enum_name,members);
    if(e == NULL) goto end;
    if(PyModule_AddObject(module,enum_name + sizeof(FULL_MODULE_STR),e)) {
        Py_DECREF(e);
        goto end;
    }

    for(; e_i < size; ++e_i) {
        dest[e_i] = PyObject_GetAttr(e,str_objs[e_i]);
        if(dest[e_i] == NULL) {
            decref_array(dest,e_i);
            dest[0] = NULL; /* this is how "module_exec" knows that this array is not initialized */
            goto end;
        }
    }

    r = 0;

  end:
    Py_XDECREF(members);
    decref_array(str_objs,str_i);
    PyMem_Free(str_objs);
    return r;
}

static int module_exec(PyObject *module) {
    PyObject *field_name_objs[PY_FIELD_COUNT];
    PyObject *enum_base=NULL;
    PyObject *frozen_list_bases=NULL;
    PyObject *node_bases=NULL;
    PyObject *fields_str=NULL;
    size_t i;
    size_t class_i=0;
    size_t tu_i=0;
    size_t char_i=0;
    size_t field_i=0;
    module_state *state = PyModule_GetState(module);

    for(; tu_i<TAGGED_UNION_NAME_COUNT; ++tu_i) {
        state->tag_names[tu_i] = PyUnicode_FromString(tagged_union_names[tu_i]);
        if(state->tag_names[tu_i] == NULL) goto error;
    }

//% if char_enum_chars
    for(; char_i<ENUM_CHAR_COUNT; ++char_i) {
        state->char_objects[char_i] = PyUnicode_FromStringAndSize(&enum_chars[char_i],1);
        if(state->char_objects[char_i] == NULL) goto error;
    }
//% endif

    enum_base = load_class_from_mod("Enum","enum");
    if(enum_base == NULL) goto error;
//% for type in types|select('enumeration_t')
    if(create_enum(
        state->enum_values__{$ type $},
        module,
        enum_base,
        FULL_MODULE_STR ".{$ type $}",
        enum_{$ 'id' if type.any_renamed else 'value' $}_str__{$ type $},
        enum_value_str__{$ type $},
        ENUM_VALUE_COUNT__{$ type $})) goto error;
//% endfor
    Py_DECREF(enum_base);
    enum_base = NULL;

    for(; field_i<PY_FIELD_COUNT; ++field_i) {
        field_name_objs[field_i] = PyUnicode_FromString(py_field_names[field_i]);
        if(field_name_objs[field_i] == NULL) goto error;
    }

    fields_str = PyUnicode_FromString("_fields");
    if(fields_str == NULL) goto error;

    for(i=0; i<CLASS_COUNT; ++i) {
        if(i == CLASS_FROZEN_LIST) {
            assert(state->classes[CLASS_NODE] != NULL);

            state->classes[i] = (PyTypeObject*)PyType_FromSpec(&class_specs[i].spec);
            if(state->classes[i] == NULL) goto error;
            ++class_i;

            frozen_list_bases = PyTuple_New(2);
            if(frozen_list_bases == NULL) goto error;

            PyTuple_SetItem(frozen_list_bases,0,(PyObject*)state->classes[CLASS_NODE]);
            Py_INCREF(state->classes[CLASS_NODE]);
            PyTuple_SetItem(frozen_list_bases,1,(PyObject*)state->classes[i]);
            Py_INCREF(state->classes[i]);

            node_bases = PyTuple_New(1);
            if(node_bases == NULL) goto error;
            PyTuple_SetItem(node_bases,0,(PyObject*)state->classes[CLASS_NODE]);
            Py_INCREF(state->classes[CLASS_NODE]);
        } else if(i == CLASS_PARSE_ERROR) {
            PyObject *bases = PyTuple_New(1);
            if(bases == NULL) goto error;
            PyTuple_SetItem(bases,0,PyExc_RuntimeError);
            Py_INCREF(PyExc_RuntimeError);
            state->classes[i] = (PyTypeObject*)PyType_FromSpecWithBases(&class_specs[i].spec,bases);
            Py_DECREF(bases);
            if(state->classes[i] == NULL) goto error;
            ++class_i;
        } else if(i == CLASS_PARSE_WARNING) {
            PyObject *bases = PyTuple_New(1);
            if(bases == NULL) goto error;
            PyTuple_SetItem(bases,0,PyExc_UserWarning);
            Py_INCREF(PyExc_UserWarning);
            state->classes[i] = (PyTypeObject*)PyType_FromSpecWithBases(&class_specs[i].spec,bases);
            Py_DECREF(bases);
            if(state->classes[i] == NULL) goto error;
            ++class_i;
        } else if(class_specs[i].type == CLASS_TYPE_OTHER) {
            state->classes[i] = (PyTypeObject*)PyType_FromSpec(&class_specs[i].spec);
            if(state->classes[i] == NULL) goto error;
            ++class_i;
        } else {
            PyObject *field_tuple;
            int r=0;

            assert(frozen_list_bases != NULL && node_bases != NULL);
            state->classes[i] = (PyTypeObject*)PyType_FromSpecWithBases(
                &class_specs[i].spec,
                class_specs[i].type == CLASS_TYPE_LIST_NODE_SUB ? frozen_list_bases : node_bases);
            
            if(state->classes[i] == NULL) goto error;
            ++class_i;

            assert((class_specs[i].field_count == 0) == (class_specs[i].assign_field_name_tuple == NULL));
            field_tuple = PyTuple_New(class_specs[i].field_count);
            if(field_tuple == NULL) goto error;
            if(class_specs[i].field_count) {
#ifndef NDEBUG
                Py_ssize_t count = class_specs[i].assign_field_name_tuple(field_tuple,field_name_objs,0);
                assert(count == class_specs[i].field_count);
#else
                class_specs[i].assign_field_name_tuple(field_tuple,field_name_objs,0);
#endif
            }
            r = PyObject_SetAttr((PyObject*)state->classes[i],fields_str,field_tuple);
            Py_DECREF(field_tuple);
            if(r < 0) goto error;
        }

        if(PyModule_AddObject(module,class_specs[i].spec.name + sizeof(FULL_MODULE_STR),(PyObject*)state->classes[i])) {
            goto error;
        };
        Py_INCREF(state->classes[i]);
    }

    Py_DECREF(frozen_list_bases);
    Py_DECREF(node_bases);
    Py_DECREF(fields_str);
    decref_array(field_name_objs,field_i);

    return 0;

  error:
    Py_XDECREF(fields_str);
    Py_XDECREF(enum_base);
    Py_XDECREF(node_bases);
    Py_XDECREF(frozen_list_bases);
//% for type in types|select('enumeration_t')
    if(state->enum_values__{$ type $}[0] != NULL) decref_array(state->enum_values__{$ type $},ENUM_VALUE_COUNT__{$ type $});
//% endfor
    decref_array(field_name_objs,field_i);
    decref_array((PyObject**)state->classes,class_i);
    decref_array(state->tag_names,tu_i);
    decref_array(state->char_objects,char_i);
    state->classes[0] = NULL;
    return -1;
}

static int module_traverse(PyObject *module,visitproc visit,void *arg) {
    int r;
    module_state *state = PyModule_GetState(module);
    if(state == NULL) return 0;

//% for type in types|select('enumeration_t')
    r = visit_array(state->enum_values__{$ type $},ENUM_VALUE_COUNT__{$ type $},visit,arg);
    if(r) return r;
//% endfor
    return visit_array((PyObject**)state->classes,CLASS_COUNT,visit,arg);
}

static void module_free(void *module) {
    module_state *state = PyModule_GetState(module);
    if(state->classes[0] == NULL) return;

//% for type in types|select('enumeration_t')
    decref_array(state->enum_values__{$ type $},ENUM_VALUE_COUNT__{$ type $});
//% endfor
    decref_array((PyObject**)state->classes,CLASS_COUNT);
    decref_array(state->tag_names,TAGGED_UNION_NAME_COUNT);
    decref_array(state->char_objects,ENUM_CHAR_COUNT);
}

/* Python 3.7 doesn't offer a way to get per-module state if multi-phase
initialization is used, so for now, single-phase initialization is used.

static PyModuleDef_Slot m_slots[] = {
    {Py_mod_exec,module_exec},
#if PARSER_PY_VERSION_HEX >= 0x030c00f0
    {Py_mod_multiple_interpreters,Py_MOD_PER_INTERPRETER_GIL_SUPPORTED},
#endif
    {0,NULL}};
*/

static PyModuleDef module_def = {
    .m_base = PyModuleDef_HEAD_INIT,
    .m_name = FULL_MODULE_STR,
    .m_size = sizeof(module_state),
    .m_methods = module_functions,
    /*.m_slots = m_slots,*/
    .m_traverse = module_traverse,
    .m_free = module_free};

PyMODINIT_FUNC MAKE_INIT_FUN_NAME(MODULE_NAME)(void) {
    /*return PyModuleDef_Init(&module_def);*/
    PyObject *r = PyModule_Create(&module_def);
    if(module_exec(r)) {
        Py_DECREF(r);
        return NULL;
    }
    return r;
}
