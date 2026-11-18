#pragma once

#include "module/module.h"

// Root namespace of this project's game module. Everything the module registers lives below it; scripts and construct
// packs are found by reflection (declare them in headers: the generated entry point includes every header in game/).



namespace test_project {

    // Called by the engine after the module's scripts and packs are registered
    void on_module_load(GLT::modules::context& ctx);

    // Called by the engine first on unload. Everything registered under ctx.id is removed by the engine afterwards
    void on_module_unload(GLT::modules::context& ctx);

}
