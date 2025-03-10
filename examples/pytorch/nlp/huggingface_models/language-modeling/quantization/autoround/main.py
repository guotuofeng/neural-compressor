import argparse

from neural_compressor.adaptor.torch_utils.autoround import AutoRound, AutoOPTRound, AutoAdamRound

parser = argparse.ArgumentParser()
import torch
import os

os.environ["CUBLAS_WORKSPACE_CONFIG"] = ":4096:8"
torch.use_deterministic_algorithms(True, warn_only=True)
from transformers import AutoModelForCausalLM, AutoTokenizer, AutoModel

from transformers import set_seed

from eval import eval_model

import re

os.environ["TOKENIZERS_PARALLELISM"] = "false"



if __name__ == '__main__':

    parser.add_argument(
        "--model_name", default="/models/opt-125m"
    )

    parser.add_argument("--num_bits", default=4, type=int,
                        help="number of  bits")

    parser.add_argument("--group_size", default=128, type=int,
                        help="group size")

    parser.add_argument("--train_bs", default=8, type=int,
                        help="train batch size")

    parser.add_argument("--eval_bs", default=4, type=int,
                        help="eval batch size")

    parser.add_argument("--device", default=0, type=str,
                        help="device gpu int number, or 'cpu' ")
    #
    parser.add_argument("--sym", action='store_true',
                         help=" sym quantization")

    parser.add_argument("--iters", default=400, type=int,
                        help=" iters")

    parser.add_argument("--use_quant_input", action='store_true',
                        help="whether to use the output of quantized block to tune the next block")

    parser.add_argument("--lr", default=0.05, type=float,
                        help="step size")

    parser.add_argument("--minmax_lr", default=None, type=float,
                        help="minmax learning rate, if None,it will set to be the same with lr")

    parser.add_argument("--seed", default=42, type=int,
                        help="seed")

    parser.add_argument("--eval_fp16_baseline", action='store_true',
                        help="whether to eval FP16 baseline")

    parser.add_argument("--amp", action='store_true',
                        help="amp")

    parser.add_argument("--adam", action='store_true',
                        help="adam")

    parser.add_argument("--seqlen", default=2048, type=int,
                        help="sequence length")

    parser.add_argument("--gradient_accumulate_steps", default=1, type=int, help="gradient accumulate steps")

    parser.add_argument("--n_blocks", default=1, type=int, help="num of blocks to tune together")

    parser.add_argument("--n_samples", default=512, type=int,
                        help="number of samples")

    parser.add_argument("--low_gpu_mem_usage", action='store_true',
                        help="low_gpu_mem_usage")

    parser.add_argument("--enable_minmax_tuning", action='store_true',
                        help="whether enable weight minmax tuning")

    # parser.add_argument("--tasks", default=["lambada_openai", "hellaswag", "winogrande", "piqa"],
    #                     help="lm-eval tasks")

    # parser.add_argument("--tasks", default=["lambada_openai"],
    #                     help="lm-eval tasks")

    # parser.add_argument("--tasks",
    #                     default=['wikitext2', 'ptb-new', 'c4-new', 'lambada_openai', 'hellaswag', 'winogrande', 'piqa',
    #                              'coqa', 'truthfulqa_mc', 'openbookqa', 'boolq', 'rte', 'arc_easy', 'arc_challenge',
    #                              'hendrycksTest-*', 'wikitext', 'drop', 'gsm8k'],##all
    parser.add_argument("--tasks",
                        default=['wikitext2', 'ptb-new', 'c4-new', 'lambada_openai', 'hellaswag', 'winogrande', 'piqa',
                                 "hendrycksTest-*", "wikitext", "truthfulqa_mc", "openbookqa", "boolq", "rte",
                                 "arc_easy", "arc_challenge"],
                        help="lm-eval tasks")  # "truthfulqa_gen"

    # parser.add_argument("--tasks", default=["lambada_openai"],
    #                     help="lm-eval tasks")

    parser.add_argument("--output_dir", default="./tmp_signround", type=str,
                        help="Where to store the final model.")

    args = parser.parse_args()
    set_seed(args.seed)

    model_name = args.model_name
    if model_name[-1] == "/":
        model_name = model_name[:-1]
    print(model_name, flush=True)

    tasks = args.tasks

    if args.device == "cpu":
        device_str = "cpu"
    else:
        device_str = f"cuda:{int(args.device)}"
    cuda_device = torch.device(device_str)
    is_glm = bool(re.search("chatglm", model_name.lower()))
    if is_glm:
        model = AutoModel.from_pretrained(model_name, trust_remote_code=True)
    else:
        model = AutoModelForCausalLM.from_pretrained(
            model_name, low_cpu_mem_usage=True, torch_dtype="auto", trust_remote_code=True
        )
    model = model.eval()
    # align wigh GPTQ to eval ppl
    if "opt" in model_name:
        seqlen = model.config.max_position_embeddings
        model.seqlen = model.config.max_position_embeddings
    else:
        seqlen = 2048
        model.seqlen = seqlen
    seqlen = args.seqlen

    if "llama" in model_name:
        from transformers import LlamaTokenizer

        tokenizer = LlamaTokenizer.from_pretrained(model_name)
        if tokenizer.pad_token is None:
            tokenizer.add_special_tokens({'pad_token': '[PAD]'})
    else:
        tokenizer = AutoTokenizer.from_pretrained(model_name, trust_remote_code=True)
    if hasattr(tokenizer, "model_max_length"):
        if tokenizer.model_max_length < seqlen:
            print(f"change sequence length to {tokenizer.model_max_length} due to the limitation of model_max_length",
                  flush=True)
            seqlen = min(seqlen, tokenizer.model_max_length)
            args.seqlen = seqlen

    excel_name = f"{model_name}_{args.num_bits}_{args.group_size}"
    if args.eval_fp16_baseline:
        if not args.low_gpu_mem_usage:
            model = model.to(cuda_device)
        excel_name += "_fp16.xlsx"
        eval_model(output_dir=model_name, model=model, tokenizer=tokenizer, tasks=args.tasks, \
                   eval_bs=args.eval_bs, use_accelerate=args.low_gpu_mem_usage, device=cuda_device,
                   eval_orig_float=True, excel_file=excel_name)
        exit()

    # if args.iters <= 0:
    #     print("eval rtn", flush=True)
    #     excel_name += "_rtn.xlsx"
    #     q_dq_weight(model, num_bits=args.num_bits, group_size=args.group_size)
    #     if not args.low_gpu_mem_usage:
    #         model = model.to(cuda_device)
    #     eval_model(output_dir=args.output_dir, model=model, tokenizer=tokenizer, tasks=args.tasks, \
    #                eval_bs=args.eval_bs, use_accelerate=args.low_gpu_mem_usage, device=cuda_device,
    #                excel_file=excel_name)
    #     exit()

    if not args.low_gpu_mem_usage:
        model = model.to(cuda_device)

    scheme = "asym"
    if args.sym:
        scheme = "sym"
    round = AutoRound
    if args.adam:
        round = AutoAdamRound

    optq = round(model, tokenizer, args.num_bits, args.group_size, scheme, bs=args.train_bs,
                 seqlen=seqlen, n_blocks=args.n_blocks, iters=args.iters, lr=args.lr,
                 minmax_lr=args.minmax_lr, use_quant_input=args.use_quant_input,
                 amp=args.amp, n_samples=args.n_samples, low_gpu_mem_usage=args.low_gpu_mem_usage, seed=args.seed, gradient_accumulate_steps=args.gradient_accumulate_steps)  ##TODO args pass
    optq.quantize()

    torch.cuda.empty_cache()
    model.eval()
    output_dir = args.output_dir + "_" + args.model_name.split('/')[-1] + f"_w{args.num_bits}_g{args.group_size}"

    # model.to(cuda_device)
    # eval_model(model, model_name, tokenizer, tasks=args.tasks, eval_bs=args.eval_bs)
    excel_name = f"{output_dir}_result.xlsx"
    output_dir += "/"
    print(excel_name, flush=True)
    eval_model(output_dir=output_dir, model=model, tokenizer=tokenizer, tasks=args.tasks, \
               eval_bs=args.eval_bs, use_accelerate=args.low_gpu_mem_usage, device=cuda_device, excel_file=excel_name,
               limit=None)
