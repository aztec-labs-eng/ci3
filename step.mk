# ci3 steps: make targets whose cache key ci3 derives from the make dependency graph (see ci3/step).
#
# Include it in a repository's Makefile once ROOT is the repository root:
#   include $(ROOT)/ci3/step.mk
#
# A step names its own inputs, and make's graph supplies the rest of its key:
#   avm-transpiler-native: private INPUTS := avm-transpiler/.rebuild_patterns
#   avm-transpiler-native: | noir-format-check
#   	$(call step,$@,avm-transpiler,build_native)
#
# The command writes its outputs to $STEP_OUT (<project>/ci3-out/<target>), which is what is cached, and what
# anything using them reads. The repository's .gitignore needs ci3-out/.
#
# - Normal prerequisites (before |) are part of the key: the step uses what they produce.
# - Order-only prerequisites (after |) only run first, as a check or a warm-up does.
# - Targets may also set KEY_ENV (names of variables whose values change the outputs) and KEY_SALT.
# - `private` stops make passing a target's variables down to its prerequisites, which it otherwise does.
# - A target that only groups others, when a step depends on it, needs the recipe $(call step_group,$@).
# - A recipe that is not a step yet runs through ci3/step_legacy, so steps can depend on it.

CI3 ?= $(ROOT)/ci3

# One directory of keys per make run, shared with every recipe and with any make started under it.
ifndef CI3_KEYS_DIR
CI3_KEYS_DIR := $(shell mktemp -d "$${TMPDIR:-/tmp}/ci3-keys.XXXXXX")
endif
export CI3_KEYS_DIR

# $(call step,<target>,<project dir>,<bootstrap command>[,never | <paths>])
#   <project dir>        relative to ROOT
#   <bootstrap command>  run as ./bootstrap.sh <bootstrap command> on a cache miss
#   never                always run, for a command that caches per item itself
#   <paths>              transitional: cache these paths in the project instead of $STEP_OUT (see ci3/step)
define step
	@cd $(ROOT)/$(2) && INPUTS='$(INPUTS)' AFTER='$|' KEY_ENV='$(KEY_ENV)' KEY_SALT='$(KEY_SALT)' \
	  $(CI3)/color_prefix $(1) "$(CI3)/step $(1) '$(3)' '$(4)' $^"
endef

# $(call step_tests,<target>,<project dir>[,<test_cmds arguments>]): lists the project's tests for the test engine,
# keyed like a step: ./bootstrap.sh test_cmds prefixes each with $STEP_KEY.
define step_tests
	@cd $(ROOT)/$(2) && INPUTS='$(INPUTS)' AFTER='$|' KEY_ENV='$(KEY_ENV)' KEY_SALT='$(KEY_SALT)' \
	  $(CI3)/color_prefix $(1) "$(CI3)/step $(1) 'test_cmds $(3)' tests $^"
endef

# $(call step_benches,<target>,<project dir>[,<bench_cmds arguments>]): the same for benchmarks, from
# ./bootstrap.sh bench_cmds, to the file the caller names as CI3_TEST_CMDS_FILE.
define step_benches
	@cd $(ROOT)/$(2) && INPUTS='$(INPUTS)' AFTER='$|' KEY_ENV='$(KEY_ENV)' KEY_SALT='$(KEY_SALT)' \
	  $(CI3)/color_prefix $(1) "$(CI3)/step $(1) 'bench_cmds $(3)' tests $^"
endef

# $(call step_group,<target>): a target that only groups its prerequisites; its key is theirs.
define step_group
	@$(CI3)/step $(1) - never $^
endef
