#include <otawa/otawa.h>
#include <otawa/ipet.h>
#include <cassert>

using namespace elm;
using namespace otawa;

class TimeBuilder: public BBProcessor {
public:
	static p::declare reg;
	TimeBuilder(): BBProcessor(reg) { }

protected:
	void processBB(WorkSpace *ws, CFG *cfg, Block *b) override {
		if(!b->isBasic())
			return;
		BasicBlock *bb = b->toBasic();

		ot::time t = 0;
		for (auto i : *bb) {
			if (i->isMul()) {
				t += 4;
			} else if (i->isLoad()) {
				t += 5;
			} else if (i->isStore()) {
				t += 2;
			} else if (i->isControl()) {
				t += 2;
			} else if (i->isConditional()) {
				t += 2;
			} else {
				t += 1;
			}
		}
		ipet::TIME(bb) = t;

		if (isVerbose()) {
			cout << "Processed BB @" << bb->address()
				<< " with total time = " << t << io::endl;
		}
	}

};

p::declare TimeBuilder::reg = p::init("TimeBuilder", Version(1, 0, 0))
	.require(COLLECTED_CFG_FEATURE)
	.provide(ipet::BB_TIME_FEATURE);


class FlashAnalysis: public CFGProcessor {
public:
	static p::declare reg;
	FlashAnalysis(): CFGProcessor(reg) { }

protected:
	void processAll(WorkSpace *ws) {
		if (isVerbose()) {
			cout << "Starting FlashAnalysis..." << io::endl;
		}

		Block *entry = cfgs().entry()->entry();
		if (!entry) {
			cerr << "No entry block available in the CFG!" << io::endl;
			return;
		}

		int totalAccesses = 0;
		int totalMisses = 0;
		Vector<Block *> todo;
		OUT.set(entry, TOP);

		for (auto e : entry->outEdges()) {
			todo.push(e->sink());
		}

		while (!todo.isEmpty()) {
			Block *b = todo.pop();
			if (b->isBasic()) {
				BasicBlock *bb = b->toBasic();
				if (processBasicBlock(bb)) {
					for (auto e : b->outEdges()) {
						todo.push(e->sink());
					}
				}
			}
		}

		adjustFlashCosts(cfgs().entry(), totalAccesses, totalMisses);

		int totalHits = totalAccesses - totalMisses;
		float hitRate = totalAccesses > 0 ? (float(totalHits) / totalAccesses) * 100 : 0.0f;
		float missRate = totalAccesses > 0 ? (float(totalMisses) / totalAccesses) * 100 : 0.0f;

		if (isVerbose()) {
			cout << "Total accesses: " << totalAccesses << io::endl;
			cout << "Total hits: " << totalHits << " (" << hitRate << "%)" << io::endl;
			cout << "Total misses: " << totalMisses << " (" << missRate << "%)" << io::endl;
		}
	}

	void processCFG(WorkSpace *ws, CFG *g) override {
	}

private:
	// TOP: unknown state
	// BOT: uncomputed state

	// Address: page address in fetch buffer
	// if one state is BOT, return the other state
	// if one state is TOP, return TOP
	// if the states differ and are concrete, return TOP
	Address join(Address s1, Address s2) {
		if (s2 == BOT || s1 == s2) {
			return s1; // Return s1 if s2 is BOT or s1 equals s2
		} else if (s1 == BOT) {
			return s2; // Return s2 if s1 is BOT
		} else {
			return TOP; // Otherwise, return TOP
		}
	}

	Address update(Address s, Inst *i) {
		// join ??
		return flashBlock(i); 
	}

	Address input(Block *v) {
		Address IN = BOT;

		for (auto e : v->inEdges()) {
			Block *pred = e->source();
			Address predOUT = OUT.get(pred, BOT);
			IN = join(IN, predOUT);
		}

		return IN;
	}

	Address flashBlock(Inst *i) {
		return i->address().mask(mask);
	}

	bool processBasicBlock(BasicBlock *bb) {
		Address IN = input(bb);

		Address OUT_new = IN;
		for (auto i : *bb) {
			OUT_new = update(OUT_new, i);
		}

		Address OUT_old = OUT.get(bb, BOT);

		if (OUT_new != OUT_old) {
			OUT.set(bb, OUT_new);
			return true; // State has changed
		}
		return false; // State is the same
	}

	void adjustFlashCosts(CFG *cfg, int &totalAccesses, int &totalMisses) {
		for (CFG::BlockIter bb = cfg->blocks(); !bb.ended(); bb.next()) {
			Block *b = *bb;
			if (b->isBasic()) {
				BasicBlock *basicBlock = b->toBasic();
				ot::time flashCost = 0;
				Address bufferState = OUT.get(basicBlock, BOT);

				for (auto i : *basicBlock) {
					Address page = flashBlock(i);
					totalAccesses++;
					if (bufferState != page) {
						flashCost += 20;
						bufferState = page;
						totalMisses++;
					}
				}

				ipet::TIME(basicBlock) += flashCost;

				if (isVerbose()) {
					cout << "Adjusted TIME for BB @" << basicBlock->address()
						<< ": + " << flashCost << " cycles for flash memory." << io::endl;
				}
			}
		}
	}


	static p::id<Address> OUT;
	static const Address TOP, BOT;
	static const t::uint32 mask = 32 - 1;
	static const ot::time cost = 10;
};

p::declare FlashAnalysis::reg = p::init("FlashAnalysis", Version(1, 0, 0))
	.require(COLLECTED_CFG_FEATURE)
	.require(ipet::BB_TIME_FEATURE);

p::id<Address> FlashAnalysis::OUT("", BOT);
const Address FlashAnalysis::TOP(-1, -2);
const Address FlashAnalysis::BOT;


int main(int argc, char **argv) {
	// check if we have exactly one parameter!
	if(argc != 2) {
		cerr << "SYNTAX: " << argv[0] << " ELF_FILE\n";
		return 1;
	}

	try {

		// this proplist is used to pass option for ELF file opening
		PropList props;

		// comment this to avoid verbose display of OTAWA
		VERBOSE(props) = true;

		// open the binary file and store it in a workspace
		WorkSpace *ws = MANAGER.load(argv[1], props);

		// compute the time of blocks and store it using ipet::TIME property
		ws->run<TimeBuilder>(props);

		// flash analysis
		ws->run<FlashAnalysis>(props);

		// compute WCET
		ws->require(ipet::WCET_FEATURE, props);

		// display the WCET that has been stored on the workspace using ipet::WCET property
		cout << "WCET = " << *ipet::WCET(ws) << io::endl;

	}
	catch(elm::Exception& e) {
		cerr << "ERROR: " << e.message() << io::endl;
		return 2;
	}

	return 0;
}