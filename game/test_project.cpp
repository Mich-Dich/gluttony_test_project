#include "test_project.h"



namespace test_project {

    void on_module_load(GLT::modules::context& ctx) {

        LOG(info, "Game module [{}] loaded (project [{}])", ctx.id, ctx.project_dir.generic_string());
    }


    void on_module_unload(GLT::modules::context& ctx) {

        LOG(info, "Game module [{}] unloading", ctx.id);
    }

}
