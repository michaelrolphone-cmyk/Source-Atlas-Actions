/* Two-unit, non-production GCC-native indexing proof. No target changes. */
#include "gcc-plugin.h"
#include "plugin-version.h"
#include "tree.h"
#include "tree-iterator.h"
#include "tree-pretty-print.h"
#include "input.h"
#include <cstdio>
#include <cstdlib>
#include <cstring>
#include <string>
#include <set>
#include <map>
int plugin_is_GPL_compatible;
static FILE *out;
static std::string root, unit;
static std::set<std::string> seen;
static unsigned defs, decls, calls, indirect, missing_locations, macro_calls;
static std::map<std::string, unsigned> tree_forms;
static std::string quote(const std::string &s) {
 std::string r="\""; for (unsigned char c:s) { if(c=='"'||c=='\\') { r+='\\';r+=c; } else if(c=='\n')r+="\\n";else if(c=='\r')r+="\\r";else if(c=='\t')r+="\\t";else if(c<32) {char b[7];snprintf(b,7,"\\u%04x",c);r+=b;} else r+=c;} return r+'"';
}
static std::string name(tree t) { return t&&DECL_P(t)&&DECL_NAME(t)?IDENTIFIER_POINTER(DECL_NAME(t)):""; }
static std::string relative(const char *path) {
 if(!path)return ""; std::string s=path; if(s.compare(0,root.size()+1,root+"/")==0)return s.substr(root.size()+1);return "";
}
static std::string location(location_t loc) {
 expanded_location x=expand_location(loc); std::string p=relative(x.file);
 return "{\"file\":"+quote(x.file?x.file:"")+",\"path\":"+quote(p)+",\"line\":"+std::to_string(x.line)+",\"column\":"+std::to_string(x.column)+"}";
}
static std::string provenance(location_t loc) {
 source_range r=get_range_from_loc(line_table,loc);
 location_t spelling=linemap_resolve_location(line_table,loc,LRK_SPELLING_LOCATION,nullptr);
 return "\"location\":"+location(loc)+",\"spelling_location\":"+location(spelling)+",\"expression_range\":{\"start\":"+location(r.m_start)+",\"finish\":"+location(r.m_finish)+"},\"macro_expansion\":"+(linemap_location_from_macro_expansion_p(line_table,loc)?"true":"false");
}
static std::string type_text(tree t) {
 if(!t)return ""; char *b=nullptr;size_t n=0;FILE *s=open_memstream(&b,&n);if(!s)return "";print_generic_expr(s,t,TDF_NONE);fclose(s);std::string r(b?b:"");free(b);if(r.size()>2000)r.resize(2000);return r;
}
static std::string context_name(tree t,unsigned depth=0) {
 if(!t||depth>16||TREE_CODE(t)==TRANSLATION_UNIT_DECL)return "";
 if(TYPE_P(t)){tree n=TYPE_NAME(t);if(n&&TREE_CODE(n)==TYPE_DECL)return context_name(DECL_CONTEXT(n),depth+1)+name(n)+"::";if(n&&TREE_CODE(n)==IDENTIFIER_NODE)return std::string(IDENTIFIER_POINTER(n))+"::";return "";}
 if(DECL_P(t)) {std::string n=name(t);if(n.empty()&&TREE_CODE(t)==NAMESPACE_DECL)n="(anonymous namespace)";return context_name(DECL_CONTEXT(t),depth+1)+(n.empty()?"":n+"::");}return "";
}
static std::string decl_json(tree d) {
 return "\"uid\":"+std::to_string(DECL_UID(d))+",\"symbol\":"+quote(name(d))+",\"qualified_name\":"+quote(context_name(DECL_CONTEXT(d))+name(d))+",\"tree_code\":"+quote(get_tree_code_name(TREE_CODE(d)))+",\"type\":"+quote(type_text(TREE_TYPE(d)))+",\"artificial\":"+(DECL_ARTIFICIAL(d)?"true":"false")+","+provenance(DECL_SOURCE_LOCATION(d));
}
static void emit_decl(tree d,const char *kind) {
 if(!d||!DECL_P(d)||name(d).empty()||(DECL_ARTIFICIAL(d)&&strcmp(kind,"definition")&&strcmp(kind,"type")))return;
 expanded_location x=expand_location(DECL_SOURCE_LOCATION(d));if(relative(x.file).empty()||x.line<=0)return;
 std::string key=std::string(kind)+":"+std::to_string(DECL_UID(d))+":"+std::to_string(x.line)+":"+std::to_string(x.column);if(!seen.insert(key).second)return;
 fprintf(out,"{\"event\":%s,%s}\n",quote(kind).c_str(),decl_json(d).c_str());if(!strcmp(kind,"definition"))defs++;else decls++;
}
static void finish_decl(void *data,void*) {emit_decl((tree)data,"declaration");}
static void finish_type(void *data,void*) {
 tree t=(tree)data;if(!t||!TYPE_P(t))return;
 tree n=TYPE_NAME(t);if(n&&TREE_CODE(n)==TYPE_DECL)emit_decl(n,"type");
 if(TREE_CODE(t)==RECORD_TYPE||TREE_CODE(t)==UNION_TYPE)for(tree f=TYPE_FIELDS(t);f;f=DECL_CHAIN(f))if(DECL_P(f))emit_decl(f,"member");
}
struct walk_context { tree caller; };
static tree visit(tree *node,int *walk_subtrees,void *data) {
 tree t=*node;if(!t)return NULL_TREE;
 if(TYPE_P(t)||DECL_P(t)){*walk_subtrees=0;return NULL_TREE;}
 tree_forms[get_tree_code_name(TREE_CODE(t))]++;
 if(TREE_CODE(t)!=CALL_EXPR)return NULL_TREE;
 walk_context *ctx=(walk_context*)data;location_t loc=EXPR_LOCATION(t);expanded_location x=expand_location(loc);
 if(loc==UNKNOWN_LOCATION||x.line<=0){missing_locations++;return NULL_TREE;}
 if(relative(x.file).empty())return NULL_TREE;
 tree target=get_callee_fndecl(t);
 if(!target||TREE_CODE(target)!=FUNCTION_DECL){indirect++;fprintf(out,"{\"event\":\"unresolved_indirect_call\",\"caller_uid\":%u,\"caller_symbol\":%s,%s}\n",DECL_UID(ctx->caller),quote(name(ctx->caller)).c_str(),provenance(loc).c_str());return NULL_TREE;}
 if(linemap_location_from_macro_expansion_p(line_table,loc))macro_calls++;
 fprintf(out,"{\"event\":\"direct_call\",\"caller_uid\":%u,\"caller_symbol\":%s,\"target\":{%s},%s}\n",DECL_UID(ctx->caller),quote(name(ctx->caller)).c_str(),decl_json(target).c_str(),provenance(loc).c_str());calls++;return NULL_TREE;
}
static void pre_genericize(void *data,void*) {
 tree fn=(tree)data;if(!fn||TREE_CODE(fn)!=FUNCTION_DECL)return;
 expanded_location x=expand_location(DECL_SOURCE_LOCATION(fn));if(relative(x.file).empty())return;
 emit_decl(fn,"definition");for(tree p=DECL_ARGUMENTS(fn);p;p=DECL_CHAIN(p))emit_decl(p,"parameter");
 tree body=DECL_SAVED_TREE(fn);if(body){walk_context ctx{fn};walk_tree_without_duplicates(&body,visit,&ctx);}
}
static void finish(void*,void*) {
 if(!out)return;
 fprintf(out,"{\"event\":\"tree_form_inventory\",\"common_walker_exhaustiveness_proved\":false,\"forms\":{");bool first=true;for(const auto &p:tree_forms){fprintf(out,"%s%s:%u",first?"":",",quote(p.first).c_str(),p.second);first=false;}fprintf(out,"}}\n");
 fprintf(out,"{\"event\":\"summary\",\"definitions\":%u,\"declarations_and_types\":%u,\"direct_calls\":%u,\"unresolved_indirect_calls\":%u,\"missing_call_locations\":%u,\"macro_calls\":%u,\"semantic_coverage_complete\":false,\"unsupported\":[\"cross-translation-unit symbol identity\",\"uninstantiated templates\",\"variable read/write/address references\",\"indirect target resolution\",\"macro reference equivalence\",\"inheritance and complete type-use relationships\",\"semantic equivalence to Clang\",\"exhaustive C++ tree walking\",\"all redeclaration occurrences\",\"inactive branches and folded-away references\",\"exhaustive templates constructors destructors and lambdas\"]}\n",defs,decls,calls,indirect,missing_locations,macro_calls);fclose(out);out=nullptr;
}
extern "C" int plugin_init(struct plugin_name_args *info,struct plugin_gcc_version *version) {
 if(!plugin_default_version_check(version,&gcc_version))return 1;
 const char *path=nullptr;
 for(int i=0;i<info->argc;i++){std::string k=info->argv[i].key;const char *v=info->argv[i].value;if(!v)return 1;if(k=="output")path=v;else if(k=="root")root=v;else if(k=="unit")unit=v;else return 1;}
 if(!path||root.empty()||unit.empty())return 1;out=fopen(path,"w");if(!out)return 1;
 fprintf(out,"{\"event\":\"metadata\",\"schema\":\"gcc-native-proof/1\",\"gcc_version\":%s,\"gcc_datestamp\":%s,\"unit\":%s,\"identity_scope\":\"DECL_UID is process-local; no production symbol identity claimed; type text and context are callback-local diagnostic renderings\"}\n",quote(version->basever).c_str(),quote(version->datestamp).c_str(),quote(unit).c_str());
 register_callback(info->base_name,PLUGIN_FINISH_DECL,finish_decl,nullptr);
 register_callback(info->base_name,PLUGIN_FINISH_TYPE,finish_type,nullptr);
 register_callback(info->base_name,PLUGIN_PRE_GENERICIZE,pre_genericize,nullptr);
 register_callback(info->base_name,PLUGIN_FINISH,finish,nullptr);return 0;
}
